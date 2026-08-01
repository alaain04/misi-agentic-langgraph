from __future__ import annotations

from datetime import UTC, datetime

from src.main_graph.config import PipelineConfigurable
from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.models.results import AgentCallRecord, AgentDispatch, PrepResult


async def run_specialist(
    agent_type: str,
    dispatch: AgentDispatch,
    prep: PrepResult,
    svc: PipelineConfigurable,
) -> tuple[str, dict]:
    """Runs one specialist agent, saves its bundle, and returns
    (bundle_id, AgentCallRecord.model_dump())."""
    agent_class = REGISTRY[agent_type]
    started_at = datetime.now(UTC).isoformat()
    bundle, tools_used, react_iterations = await agent_class().run(
        dispatch, prep, svc["container"], cache=svc.get("input_cache")
    )
    finished_at = datetime.now(UTC).isoformat()
    bundle_id = await svc["result_dao"].save_bundle(bundle)
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
    return bundle_id, record.model_dump()
