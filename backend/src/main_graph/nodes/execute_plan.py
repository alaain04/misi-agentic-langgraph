"""Execute plan node — dispatches to the appropriate skeleton subgraph by name."""

import logging

from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_REGISTRY
from src.services.job_dao import JobDAO

logger = logging.getLogger(__name__)


async def execute_plan(state: MainState) -> dict:
    name = state.get("subgraph_name", "")
    job_id = state.get("job_id", "")
    dao = JobDAO()

    subgraph = SUBGRAPH_REGISTRY.get(name)

    if subgraph is None:
        logger.warning("execute_plan: unknown subgraph %r", name)
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
                "upstream_results": state.get("upstream_results", {}),
            }
        )
        if job_id:
            await dao.update_artifact_data(job_id, name, {"result": result})
            await dao.complete_artifact(job_id, name, "done")
        logger.info("execute_plan: %s completed", name)
        return {"subgraph_results": [{"subgraph": name, "data": result}]}
    except Exception:
        logger.exception("execute_plan: %s failed", name)
        if job_id:
            await dao.complete_artifact(job_id, name, "failed")
        raise
