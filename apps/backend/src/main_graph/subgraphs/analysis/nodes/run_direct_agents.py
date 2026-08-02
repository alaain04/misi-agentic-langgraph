from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import PipelineConfigurable, get_services
from src.main_graph.subgraphs.analysis.concern import Concern, whole_tree_agents
from src.main_graph.subgraphs.analysis.deepagent.limits import SPECIALIST_SEMAPHORE
from src.main_graph.subgraphs.analysis.deepagent.specialist_runner import run_specialist
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AgentDispatch, PrepResult


async def _run_one(
    agent_type: str,
    concern: Concern,
    hypothesis: str,
    prep: PrepResult,
    svc: PipelineConfigurable,
) -> tuple[str, dict]:
    dispatch = AgentDispatch(
        domain=", ".join(concern.type),
        hypothesis=hypothesis,
        packages_to_focus=[],  # ignored by whole-tree agents anyway
        agent_type=agent_type,
    )
    async with SPECIALIST_SEMAPHORE:
        return await run_specialist(agent_type, dispatch, prep, svc)


async def run_direct_agents(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    prep = await svc["result_dao"].get_prep(state["prep_result_id"])
    concern = Concern(**state["structured_concern"])

    results = await asyncio.gather(
        *[
            _run_one(agent_type, concern, state["concern"], prep, svc)
            for agent_type in whole_tree_agents(concern)
        ]
    )

    return {
        "bundle_ids": [bundle_id for bundle_id, _ in results],
        "agent_calls": [record for _, record in results],
    }
