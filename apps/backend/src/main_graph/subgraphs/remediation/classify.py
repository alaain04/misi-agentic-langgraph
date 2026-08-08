"""Classifies each remediation target into a tier (r1/r2/r3) from its
GitHub release notes alone, before the expensive per-target investigate-
and-edit subagent ever runs. Tier is carried as a hint on each target --
see docs/superpowers/specs/2026-08-02-remediation-tier-classification.md."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal, cast

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes
from src.main_graph.subgraphs.remediation.selection import select_remediation_targets
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.models.remediation import RemediationTarget
from src.utils.config import settings
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_CLASSIFY_SYSTEM_PROMPT = """\
You classify an npm dependency remediation into exactly one tier, from its \
GitHub release notes:

- r1: a same-package version bump with no breaking changes relevant to a \
typical consumer. Safe to bump without touching calling code.
- r2: a same-package version bump whose release notes describe breaking \
changes (removed/renamed APIs, changed defaults, new required config, \
major-version markers, etc.) that would plausibly require adapting calling \
code.
- r3: the release notes (or the absence of further releases) indicate this \
package is deprecated, abandoned, or explicitly superseded by a different \
package -- a same-package bump is not the right fix at all, only migrating \
to a replacement dependency is.

Prefer r1 unless the release notes give a concrete reason to classify \
otherwise. r3 is reserved for an explicit migration signal, not merely \
"has a major version"."""


class TargetClassification(BaseModel):
    tier: Literal["r1", "r2", "r3"]
    rationale: str


async def classify_target(
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> TargetClassification:
    try:
        release_notes = await fetch_release_notes(
            target.target_dep, repo_path, container, docker_image
        )
        structured = _llm.with_structured_output(
            TargetClassification, method="function_calling"
        )
        return cast(
            TargetClassification,
            await structured.ainvoke(
                [
                    {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Dependency: {target.target_dep}\n"
                            f"Current range: {target.current_range or 'unknown'}\n"
                            f"Release notes: {json.dumps(release_notes)[:4000]}"
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
        return TargetClassification(
            tier="r2",
            rationale=f"classification failed, defaulting conservatively: {exc}",
        )


_MAX_CONCURRENT_CLASSIFICATIONS = 6


async def _classify_bounded(
    semaphore: asyncio.Semaphore,
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> TargetClassification:
    async with semaphore:
        return await classify_target(target, repo_path, container, docker_image)


async def classify_targets_node(
    state: RemediationState, config: RunnableConfig
) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])
    analysis = await dao.get_analysis(state["analysis_result_id"])

    initial = select_remediation_targets(
        analysis.findings, prep.dependency_graph, settings.risk_min_severity
    )
    if not initial:
        return {"targets": {}, "remediations": {}}

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CLASSIFICATIONS)
    classifications = await asyncio.gather(
        *[
            _classify_bounded(
                semaphore, t, prep.repo_path, container, prep.docker_image
            )
            for t in initial
        ]
    )

    targets: dict[str, dict] = {}
    for target, classification in zip(initial, classifications, strict=True):
        target.tier = classification.tier
        targets[target.target_dep] = target.model_dump()

    return {"targets": targets, "remediations": {}}
