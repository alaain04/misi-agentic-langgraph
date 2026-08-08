"""Investigation phase (spec D2): deterministic Dependency + Source
investigators and an LLM-digested Release investigator, combined per target
into a TargetInvestigation that the plan_and_orchestrate deepagent reads."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import cast

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.dependency_graph import dependents_of
from src.main_graph.subgraphs.remediation.changelog import (
    fetch_release_notes_between,
)
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.tools.search_code import find_local_usage_sites
from src.models.remediation import ReleaseDigest, RemediationTarget, TargetInvestigation
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_RELEASE_SYSTEM_PROMPT = """\
You are the release investigator for an npm dependency upgrade. Given the \
GitHub release notes for the versions BETWEEN the installed version and the \
target version, decide whether upgrading requires any code change in a \
consumer, and if so produce a concise migration guide.

Set migration_needed=true ONLY when the notes describe a breaking change a \
typical consumer would have to adapt to (removed/renamed API, changed \
default, new required config, etc.). A pure bug/patch/feature release with \
no consumer-facing break is migration_needed=false with an empty guide. \
List each concrete breaking change in breaking_changes. Keep migration_guide \
short and specific to what a caller must change."""


async def investigate_release(
    target_dep: str,
    from_version: str | None,
    to_version: str | None,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> ReleaseDigest:
    try:
        notes = await fetch_release_notes_between(
            target_dep, from_version, to_version, repo_path, container, docker_image
        )
        # If release notes fetch failed, return conservative digest without LLM.
        if not notes.get("available"):
            logger.warning(
                "investigate_release: notes unavailable for %s (%s->%s): %s; "
                "assuming breaking",
                target_dep,
                from_version,
                to_version,
                notes.get("error"),
            )
            error = notes.get("error")
            reason = f"release notes unavailable, assuming breaking: {error}"
            return ReleaseDigest(
                from_version=from_version,
                to_version=to_version,
                migration_needed=True,
                migration_guide="",
                breaking_changes=[reason],
            )
        structured = _llm.with_structured_output(
            ReleaseDigest, method="function_calling"
        )
        digest = cast(
            ReleaseDigest,
            await structured.ainvoke(
                [
                    {"role": "system", "content": _RELEASE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Dependency: {target_dep}\n"
                            f"From version: {from_version or 'unknown'}\n"
                            f"To version: {to_version or 'unknown'}\n"
                            f"Release notes: {json.dumps(notes)[:6000]}"
                        ),
                    },
                ]
            ),
        )
        # Trust the LLM's decision but pin the versions to what we asked about.
        digest.from_version = from_version
        digest.to_version = to_version
        return digest
    except Exception as exc:
        logger.warning(
            "investigate_release: failed for %s (%s->%s): %s; assuming breaking",
            target_dep,
            from_version,
            to_version,
            exc,
        )
        reason = f"release investigation failed, assuming breaking: {exc}"
        return ReleaseDigest(
            from_version=from_version,
            to_version=to_version,
            migration_needed=True,
            migration_guide="",
            breaking_changes=[reason],
        )


def investigate_dependents(dependency_graph: dict, target_dep: str) -> list[str]:
    """Deterministic Dependency investigator: packages in the tree that
    depend on target_dep (structural, from the resolved graph)."""
    return dependents_of(dependency_graph, target_dep)


def investigate_call_sites(repo_path: str, target_dep: str) -> list[str]:
    """Deterministic Source investigator: repo files that mention target_dep,
    sorted. Reuses the local substring scan (no container, no LLM)."""
    return sorted(
        {hit["file"] for hit in find_local_usage_sites(repo_path, target_dep)}
    )


def _resolve_versions(
    target: RemediationTarget, dependency_graph: dict
) -> tuple[str | None, str | None]:
    """(from_version, to_version). from = installed version from the graph;
    to = None until analysis-finding version-enrichment supplies fixed_version
    (release fetch then degrades to the unfiltered recent set, spec soft-dep)."""
    installed = (dependency_graph.get("direct") or {}).get(target.target_dep)
    return (installed, None)


async def investigate_target(
    target: RemediationTarget,
    repo_path: str,
    dependency_graph: dict,
    container: ContainerRunPort,
    docker_image: str,
) -> TargetInvestigation:
    """Combine all three investigators (deterministic Dependency + Source, and
    LLM-digested Release) into a TargetInvestigation."""
    from_version, to_version = _resolve_versions(target, dependency_graph)
    release = await investigate_release(
        target.target_dep,
        from_version,
        to_version,
        repo_path,
        container,
        docker_image,
    )
    return TargetInvestigation(
        target_dep=target.target_dep,
        dependents=investigate_dependents(dependency_graph, target.target_dep),
        call_sites=investigate_call_sites(repo_path, target.target_dep),
        release=release,
    )


_MAX_CONCURRENT_INVESTIGATIONS = 6  # matches classify; guards against 429s


async def investigate_node(state: RemediationState, config: RunnableConfig) -> dict:
    """Fan out investigate_target over all targets with bounded concurrency."""
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    targets = state.get("targets") or {}
    if not targets:
        return {"investigations": {}}
    prep = await dao.get_prep(state["prep_result_id"])

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_INVESTIGATIONS)

    async def _bounded(target_dict: dict) -> TargetInvestigation:
        target = RemediationTarget(**target_dict)
        async with semaphore:
            return await investigate_target(
                target,
                prep.repo_path,
                prep.dependency_graph,
                container,
                prep.docker_image,
            )

    results = await asyncio.gather(*[_bounded(t) for t in targets.values()])
    return {"investigations": {inv.target_dep: inv.model_dump() for inv in results}}
