"""Deterministic target selection for remediation: turns analysis findings
into RemediationTargets (dedup, direct-dep anchoring), resolves each
target's registry version + GitHub repo, decides the deterministic r3
"no upgrade exists" tier, and gathers blast-radius/dependents context for
the migration planner. No LLM -- see docs/superpowers/specs/2026-08-15-
remediation-release-research-agent-design.md (D-SELECT). Replaces the
former classify.py, which combined this with an LLM tier+digest call now
done by release_research.py instead."""

from __future__ import annotations

import asyncio
import logging

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.changelog import resolve_package_info
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.tools.blast_radius import compute_blast_radius
from src.models.conductor import FindingNote
from src.models.remediation import (
    FindingSummary,
    ReleaseDigest,
    RemediationTarget,
    TargetInvestigation,
)
from src.utils.config import settings
from src.utils.dependency_graph import dependents_of, direct_dependents, is_direct
from src.utils.semver import parse_semver, range_floor
from src.utils.severity import SEVERITY_ORDER

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_SELECTION = 6
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SELECTION)


def _anchors(graph: dict, dep_name: str) -> list[str]:
    if is_direct(graph, dep_name):
        return [dep_name]
    return direct_dependents(graph, dep_name)


def _filter_by_min_severity(
    findings: list[FindingNote], min_severity: str
) -> list[FindingNote]:
    """Local, explicit-threshold severity filter.

    src.utils.severity.filter_by_min_severity now reads settings.risk_min_
    severity internally instead of taking it as a parameter, which would
    make this function silently ignore its own `min_severity` argument and
    depend on global state -- breaking the "pure, deterministic" contract
    this module (and its tests) rely on. Threading the threshold explicitly
    keeps select_remediation_targets a pure function of its arguments; the
    node below is what supplies settings.risk_min_severity."""
    if min_severity == "any":
        return findings
    threshold = SEVERITY_ORDER.get(min_severity, 0)
    return [f for f in findings if SEVERITY_ORDER.get(f.severity, 0) >= threshold]


def select_remediation_targets(
    findings: list[FindingNote], dependency_graph: dict, min_severity: str
) -> list[RemediationTarget]:
    """Deterministic: filter by severity, anchor transitives to their direct
    dependent(s), unify findings that share a direct-dep bump.

    Findings with no direct anchor (no lever the user controls) are
    dropped. A dep with multiple findings (e.g. vuln + maintenance) keeps
    the highest-severity one for its FindingSummary -- ties keep whichever
    was seen first."""
    survivors = _filter_by_min_severity(findings, min_severity)
    direct = dependency_graph.get("direct") or {}

    grouped: dict[str, set[str]] = {}
    summaries: dict[str, dict[str, FindingSummary]] = {}
    for finding in survivors:
        for anchor in _anchors(dependency_graph, finding.dep_name):
            grouped.setdefault(anchor, set()).add(finding.dep_name)
            anchor_summaries = summaries.setdefault(anchor, {})
            existing = anchor_summaries.get(finding.dep_name)
            if existing is None or SEVERITY_ORDER.get(
                finding.severity, 0
            ) > SEVERITY_ORDER.get(existing.severity, 0):
                anchor_summaries[finding.dep_name] = FindingSummary(
                    dep_name=finding.dep_name,
                    severity=finding.severity,
                    description=finding.description,
                )

    return [
        RemediationTarget(
            target_dep=dep,
            addresses=sorted(addressed),
            finding_summaries=[summaries[dep][name] for name in sorted(addressed)],
            current_range=direct.get(dep),
        )
        for dep, addressed in sorted(grouped.items())
    ]


def _has_no_upgrade(current_range: str | None, latest_version: str | None) -> bool:
    """True when the registry's newest published version is not above the
    floor of the range already declared -- i.e. no same-package upgrade
    exists at all. Compares against the range's FLOOR rather than whatever
    the lockfile resolved, so this only fires when nothing higher than even
    the lowest accepted version was ever published -- conservative: it can
    force r3, never block it."""
    if not current_range or not latest_version:
        return False
    floor = range_floor(current_range)
    latest = parse_semver(latest_version)
    if floor is None or latest is None:
        return False
    return latest <= floor


async def _resolve_bounded(
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> tuple[str | None, tuple[str, str] | None]:
    async with _semaphore:
        return await resolve_package_info(
            target.target_dep, repo_path, container, docker_image
        )


async def _enrich_bounded(
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    dependency_graph: dict,
) -> TargetInvestigation:
    async with _semaphore:
        try:
            blast = await compute_blast_radius(target.target_dep, repo_path, container)
            call_sites = (
                blast.get("affected_files", []) if blast.get("available") else []
            )
        except Exception as exc:
            logger.warning(
                "_enrich_bounded: blast radius failed for %s: %s",
                target.target_dep,
                exc,
            )
            call_sites = []
        return TargetInvestigation(
            target_dep=target.target_dep,
            dependents=dependents_of(dependency_graph, target.target_dep),
            call_sites=call_sites,
            release=ReleaseDigest(
                from_version=target.current_range,
                to_version=target.latest_version,
                migration_needed=False,
            ),
        )


async def _index_codegraph(repo_path: str, container: ContainerRunPort) -> bool:
    """Build the CodeGraph blast-radius index for repo_path."""
    try:
        rc, _out, err = await container.run(
            image=settings.codegraph_docker_image,
            command="codegraph init --force /workspace",
            volume=f"{repo_path}:/workspace",
            run_as_root=True,
        )
        if rc != 0:
            logger.warning("_index_codegraph: init failed rc=%d err=%s", rc, err[:300])
            return False
    except Exception as exc:
        logger.warning("_index_codegraph: init failed: %s", exc)
        return False
    return True


async def select_targets_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])
    analysis = await dao.get_analysis(state["analysis_result_id"])

    initial = select_remediation_targets(
        analysis.findings, prep.dependency_graph, settings.risk_min_severity
    )
    if not initial:
        return {"targets": {}, "investigations": {}, "remediations": {}}

    resolved = await asyncio.gather(
        *[
            _resolve_bounded(t, prep.repo_path, container, prep.docker_image)
            for t in initial
        ]
    )
    for target, (latest_version, resolved_repo) in zip(initial, resolved, strict=True):
        target.latest_version = latest_version
        target.resolved_repo = resolved_repo
        if _has_no_upgrade(target.current_range, target.latest_version):
            target.tier = "r3"

    # Index once for the whole repo. Blast radius below degrades to empty
    # call_sites on failure (never crashes), so targets/investigations are
    # always populated regardless of whether this succeeds -- unlike the
    # classify.py bug this replaces, which left both unbound entirely.
    await _index_codegraph(prep.repo_path, container)

    investigations = await asyncio.gather(
        *[
            _enrich_bounded(t, prep.repo_path, container, prep.dependency_graph)
            for t in initial
        ]
    )

    targets: dict[str, dict] = {}
    investigations_out: dict[str, dict] = {}
    for target, investigation in zip(initial, investigations, strict=True):
        targets[target.target_dep] = target.model_dump()
        investigations_out[target.target_dep] = investigation.model_dump()

    return {
        "targets": targets,
        "investigations": investigations_out,
        "remediations": {},
    }
