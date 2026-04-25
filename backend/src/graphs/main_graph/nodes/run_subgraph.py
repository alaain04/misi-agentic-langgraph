"""Run subgraph node — dispatches to the appropriate skeleton subgraph by name."""

import logging

from src.graphs.main_graph.state import MainState
from src.graphs.recommendation import recommendation_subgraph
from src.graphs.registry import registry_subgraph
from src.graphs.repo import repo_subgraph
from src.graphs.risk_score import risk_score_subgraph
from src.graphs.runtime import runtime_subgraph
from src.services.job_dao import JobDAO

logger = logging.getLogger(__name__)

_SUBGRAPH_REGISTRY = {
    "registry": registry_subgraph,
    "repo": repo_subgraph,
    "runtime": runtime_subgraph,
    "risk_score": risk_score_subgraph,
    "recommendation": recommendation_subgraph,
}


async def run_subgraph(state: MainState) -> dict:
    name = state.get("subgraph_name", "")
    job_id = state.get("job_id", "")
    dao = JobDAO()

    subgraph = _SUBGRAPH_REGISTRY.get(name)

    if subgraph is None:
        logger.warning("run_subgraph: unknown subgraph %r", name)
        if job_id:
            await dao.complete_artifact(job_id, name, "failed")
        return {"subgraph_results": [{"subgraph": name, "error": "unknown subgraph"}]}

    if job_id:
        await dao.start_artifact(job_id, name)

    try:
        result = await subgraph.ainvoke(
            {
                "direct_dependencies": state.get("direct_dependencies", []),
                "transitive_dependencies": state.get("transitive_dependencies", []),
                "discovery_summary": state.get("discovery_summary", ""),
                "concern": state.get("concern", ""),
            }
        )
        if job_id:
            await dao.complete_artifact(job_id, name, "done")
        logger.info("run_subgraph: %s completed", name)
        return {"subgraph_results": [{"subgraph": name, "data": result}]}
    except Exception:
        logger.exception("run_subgraph: %s failed", name)
        if job_id:
            await dao.complete_artifact(job_id, name, "failed")
        raise
