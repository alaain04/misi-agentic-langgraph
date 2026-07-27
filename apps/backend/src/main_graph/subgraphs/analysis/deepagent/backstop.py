"""Deterministic, no-LLM fallback for direct deps a deep agent run left
uncovered after its corrective retry budget (spec D5, D7).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.main_graph.subgraphs.analysis.deepagent.coverage import (
    PACKAGE_SCOPED_AGENT_TYPES,
)
from src.models.results import AgentCallRecord, AgentDispatch, PrepResult

logger = logging.getLogger(__name__)

_DEFAULT_AGENT_TYPE = "web_research_agent"


def _agent_types_already_used(agent_calls: list[dict]) -> list[str]:
    used = {
        c["agent_type"]
        for c in agent_calls
        if c.get("agent_type") in PACKAGE_SCOPED_AGENT_TYPES
    }
    return sorted(used) if used else [_DEFAULT_AGENT_TYPE]


async def deterministic_backstop_dispatch(
    missing_deps: list[str],
    agent_calls: list[dict],
    prep: PrepResult,
    container,
    dao,
    cache,
    concern: str,
) -> tuple[list[str], list[dict]]:
    agent_types = _agent_types_already_used(agent_calls)
    bundle_ids: list[str] = []
    new_calls: list[dict] = []

    for dep in missing_deps:
        for agent_type in agent_types:
            agent_class = REGISTRY[agent_type]
            dispatch = AgentDispatch(
                domain="coverage_backstop",
                hypothesis=(
                    f"Deterministic backstop coverage for '{dep}' "
                    f"against concern: {concern}"
                ),
                packages_to_focus=[dep],
                agent_type=agent_type,
            )
            started_at = datetime.now(UTC).isoformat()
            try:
                bundle, tools_used, react_iterations = await agent_class().run(
                    dispatch, prep, container, cache=cache
                )
            except Exception:
                logger.warning(
                    "deterministic_backstop_dispatch: %s failed for %s",
                    agent_type,
                    dep,
                    exc_info=True,
                )
                continue
            finished_at = datetime.now(UTC).isoformat()

            bundle_id = await dao.save_bundle(bundle)
            record = AgentCallRecord(
                conductor_iteration=0,
                agent_type=agent_type,
                domain=dispatch.domain,
                packages_to_focus=dispatch.packages_to_focus,
                tools_used=tools_used,
                react_iterations=react_iterations,
                started_at=started_at,
                finished_at=finished_at,
                bundle_id=bundle_id,
            )
            bundle_ids.append(bundle_id)
            new_calls.append(record.model_dump())

    return bundle_ids, new_calls
