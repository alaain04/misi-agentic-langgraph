from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.base_agent import run_react_loop
from src.main_graph.subgraphs.analysis.agents.registry import get_agent_tools
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AgentDispatch

logger = logging.getLogger(__name__)


async def domain_agent(state: AnalysisState, config: RunnableConfig) -> dict:
    dao = get_services(config)["result_dao"]
    prep = await dao.get_prep(state["prep_result_id"])
    dispatch = AgentDispatch(**state["current_dispatch"])
    tools = get_agent_tools(dispatch.agent_type, prep)

    logger.info("domain_agent: domain=%s hypothesis=%s", dispatch.domain, dispatch.hypothesis[:60])
    bundle = await run_react_loop(dispatch, prep, tools)
    bundle_id = await dao.save_bundle(bundle)

    logger.info("domain_agent: saved bundle_id=%s findings=%d", bundle_id, len(bundle.findings))
    return {"bundle_ids": [bundle_id]}
