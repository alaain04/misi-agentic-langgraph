"""Classifies each remediation target into a tier (r1/r2/r3) and, in the
same LLM call, digests its GitHub release notes into migration guidance --
before the expensive per-target edit subagent ever runs. Tier is carried as
a hint on each target; the migration digest is carried in `investigations`
for the planner. See docs/superpowers/specs/2026-08-02-remediation-tier-
classification.md.

Release-notes digestion used to be a second pass (investigate_node) with its
own npm-view/gh-api fetch and its own LLM call per target -- redundant with
this node's own fetch for every target that isn't a deterministic r3, so
it's folded in here instead."""

from __future__ import annotations

import asyncio
import json
import logging
import textwrap
from typing import Literal, cast

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.changelog import (
    fetch_release_notes_between,
    resolve_package_info,
)
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.tools.blast_radius import compute_blast_radius
from src.models.remediation import ReleaseDigest, RemediationTarget, TargetInvestigation
from src.utils.config import settings
from src.utils.model_registry import AgentRole, get_role_llm
from src.utils.semver import parse_semver, range_floor

logger = logging.getLogger(__name__)

_llm = get_role_llm(AgentRole.REMEDIATION_CLASSIFY)

_CLASSIFY_SYSTEM_PROMPT = textwrap.dedent("""\
    You classify an npm dependency remediation into exactly one tier and,
    for a same-package bump, decide whether upgrading requires a code
    change in a consumer -- from its GitHub release notes for the versions
    BETWEEN the installed version and the target version, plus this repo's
    blast radius for the dependency (its local dependents, and the files
    that actually import or use it).

    Tier:
    - r1: a same-package version bump with no breaking changes relevant to
      a typical consumer. Safe to bump without touching calling code.
    - r2: a same-package version bump whose release notes describe breaking
      changes (removed/renamed APIs, changed defaults, new required
      config, major-version markers, etc.) that would plausibly require
      adapting calling code.
    - r3: the release notes (or the absence of further releases) indicate
      this package is deprecated, abandoned, or explicitly superseded by a
      different package -- a same-package bump is not the right fix at
      all, only migrating to a replacement dependency is.

    Prefer r1 unless the release notes give a concrete reason to classify
    otherwise. r3 is reserved for an explicit migration signal, not merely
    "has a major version".

    Migration guidance (only meaningful for r1/r2): set migration_needed
    to true ONLY when the notes describe a breaking change a typical
    consumer would have to adapt to AND that change is plausibly relevant
    given the blast radius provided. A pure bug/patch/feature release with
    no consumer-facing break sets migration_needed to false with an empty
    migration_guide -- do NOT write commentary explaining that nothing is
    needed, leave the guide empty. List each concrete breaking change in
    breaking_changes. When migration is needed, ground migration_guide in
    the actual call sites given (name the specific files/patterns to
    review) rather than generic advice. For tier=r3 set migration_needed to
    false and leave migration_guide empty -- explain why the package is
    deprecated/superseded in breaking_changes instead; a replacement
    migration plan is produced separately, not here.
    """).strip()

_MAX_CONCURRENT_CLASSIFICATIONS = 6
semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CLASSIFICATIONS)


class TargetClassification(BaseModel):
    tier: Literal["r1", "r2", "r3"]
    rationale: str
    migration_needed: bool = False
    migration_guide: str = ""
    breaking_changes: list[str] = Field(default_factory=list)


def _has_no_upgrade(current_range: str | None, latest_version: str | None) -> bool:
    """True when the registry's newest published version is not above the
    floor of the range already declared -- i.e. no same-package upgrade
    exists at all.

    Compares against the range's FLOOR ("^4.17.11" -> 4.17.11) rather than
    whatever the lockfile resolved, so this only fires when nothing higher
    than even the lowest accepted version was ever published. That makes it
    a conservative one-way signal: it can force r3, never block it.
    """
    if not current_range or not latest_version:
        return False
    floor = range_floor(current_range)
    latest = parse_semver(latest_version)
    if floor is None or latest is None:
        return False
    return latest <= floor


async def classify_target(
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> tuple[TargetClassification, TargetInvestigation]:
    if _has_no_upgrade(target.current_range, target.latest_version):
        classification = TargetClassification(
            tier="r3",
            rationale=(
                f"npm publishes no version above {target.current_range}: "
                f"latest is {target.latest_version}. No same-package "
                f"upgrade exists, so only a replacement can resolve this."
            ),
        )

    try:
        blast = await compute_blast_radius(target.target_dep, repo_path, container)

        release_notes = await fetch_release_notes_between(
            target.target_dep,
            repo_path,
            container,
            docker_image,
        )
        structured = _llm.with_structured_output(
            TargetClassification, method="function_calling"
        )
        classification = cast(
            TargetClassification,
            await structured.ainvoke(
                [
                    {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Dependency: {target.target_dep}\n"
                            f"Current range: {target.current_range or 'unknown'}\n"
                            f"To version: {target.latest_version or 'unknown'}\n"
                            f"Release notes: {json.dumps(release_notes)[:6000]}"
                        ),
                    },
                ]
            ),
        )
    except Exception as exc:
        logger.warning(
            "classify_target: classification failed for %s: %s; "
            "defaulting to r2 (conservative)",
            target.target_dep,
            exc,
        )
        classification = TargetClassification(
            tier="r2",
            rationale=f"classification failed, defaulting conservatively: {exc}",
            migration_needed=True,
            breaking_changes=[f"classification failed, assuming breaking: {exc}"],
        )

    investigation = TargetInvestigation(
        target_dep=target.target_dep,
        call_sites=blast.affected_files,
        release=ReleaseDigest(
            from_version=target.current_range,
            to_version=target.latest_version,
            migration_needed=classification.migration_needed,
            migration_guide=classification.migration_guide,
            breaking_changes=classification.breaking_changes,
        ),
    )
    return classification, investigation


async def _resolve_bounded(
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> tuple[str | None, tuple[str, str] | None]:
    async with semaphore:
        return await resolve_package_info(
            target.target_dep, repo_path, container, docker_image
        )


async def _classify_bounded(
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
    dependency_graph: dict,
) -> tuple[TargetClassification, TargetInvestigation]:
    async with semaphore:
        return await classify_target(
            target, repo_path, container, docker_image, dependency_graph
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


async def classify_targets_node(
    state: RemediationState, config: RunnableConfig
) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])
    analysis = await dao.get_analysis(state["analysis_result_id"])

    target_dependencies = analysis.findings

    resolved = await asyncio.gather(
        *[
            _resolve_bounded(target, prep.repo_path, container, prep.docker_image)
            for target in target_dependencies
        ]
    )

    for target, (latest_version, resolved_repo) in zip(
        target_dependencies, resolved, strict=True
    ):
        target.latest_version = latest_version
        target.resolved_repo = resolved_repo

    # Create a blast-radius index once for the whole repo
    is_indexed = await _index_codegraph(prep.repo_path, container)

    if is_indexed:
        results = await asyncio.gather(
            *[
                _classify_bounded(
                    target,
                    prep.repo_path,
                    container,
                    prep.docker_image,
                    prep.dependency_graph,
                )
                for target in target_dependencies
            ]
        )

        targets: dict[str, dict] = {}
        investigations: dict[str, dict] = {}
        for target, (classification, investigation) in zip(
            target_dependencies, results, strict=True
        ):
            target.tier = classification.tier
            targets[target.target_dep] = target.model_dump()
            investigations[target.target_dep] = investigation.model_dump()

    return {"targets": targets, "investigations": investigations, "remediations": {}}
