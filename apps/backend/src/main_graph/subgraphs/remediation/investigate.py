"""Investigation phase (spec D2): deterministic Dependency + Source
investigators and an LLM-digested Release investigator, combined per target
into a TargetInvestigation that the plan_and_orchestrate deepagent reads."""

from __future__ import annotations

import json
import logging
from typing import cast

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes_between
from src.models.remediation import ReleaseDigest
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
                "investigate_release: notes unavailable for %s (%s->%s): %s; assuming breaking",
                target_dep,
                from_version,
                to_version,
                notes.get("error"),
            )
            return ReleaseDigest(
                from_version=from_version,
                to_version=to_version,
                migration_needed=True,
                migration_guide="",
                breaking_changes=[
                    f"release notes unavailable, assuming breaking: {notes.get('error')}"
                ],
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
        return ReleaseDigest(
            from_version=from_version,
            to_version=to_version,
            migration_needed=True,
            migration_guide="",
            breaking_changes=[f"release investigation failed, assuming breaking: {exc}"],
        )
