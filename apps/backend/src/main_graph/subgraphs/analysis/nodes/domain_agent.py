from __future__ import annotations

import logging
from datetime import UTC, datetime

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.main_graph.subgraphs.analysis.agents.web_research_agent import WebResearchAgent
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AgentCallRecord, AgentDispatch

logger = logging.getLogger(__name__)


async def domain_agent(state: AnalysisState, config: RunnableConfig) -> dict:
    dao = get_services(config)["result_dao"]
    prep = await dao.get_prep(state["prep_result_id"])
    dispatch = AgentDispatch(**state["current_dispatch"])

    agent_class = REGISTRY.get(dispatch.agent_type, WebResearchAgent)
    agent = agent_class()

    logger.info("domain_agent: type=%s domain=%s hypothesis=%s",
                dispatch.agent_type, dispatch.domain, dispatch.hypothesis[:60])

    started_at = datetime.now(UTC).isoformat()
    bundle, tools_used, react_iterations = await agent.run(dispatch, prep)
    finished_at = datetime.now(UTC).isoformat()

    bundle_id = await dao.save_bundle(bundle)

    record = AgentCallRecord(
        conductor_iteration=state["conductor_iteration"],
        agent_type=dispatch.agent_type,
        domain=dispatch.domain,
        tools_used=tools_used,
        react_iterations=react_iterations,
        started_at=started_at,
        finished_at=finished_at,
        bundle_id=bundle_id,
    )

    logger.info("domain_agent: saved bundle_id=%s findings=%d", bundle_id, len(bundle.findings))
    return {"bundle_ids": [bundle_id], "agent_calls": [record.model_dump()]}
