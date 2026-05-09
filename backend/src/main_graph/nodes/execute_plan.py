"""Execute plan node — dispatches to the appropriate skeleton subgraph by name."""

import logging

from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import (
    SUBGRAPH_DAOS,
    SUBGRAPH_REGISTRY,
)
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
        # Hydrate upstream result IDs into actual domain data
        hydrated_upstream = {}
        for sg, result_id in state.get("upstream_results", {}).items():
            output_dao = SUBGRAPH_DAOS.get(sg)
            if output_dao and result_id:
                data = await output_dao.get(result_id)
                if data:
                    hydrated_upstream[sg] = data

        invocation: dict = {
            "direct_dependencies": state.get("direct_dependencies", []),
            "transitive_dependencies": state.get("transitive_dependencies", []),
            "discovery_summary": state.get("discovery_summary", ""),
            "concern": state.get("concern", ""),
            "upstream_results": hydrated_upstream,
        }
        if repo_path := state.get("repo_path"):
            invocation["repo_path"] = repo_path

        result = await subgraph.ainvoke(invocation)

        result_id = result.get("result_id")
        if job_id:
            await dao.update_artifact_data(job_id, name, {"result_id": result_id})
            await dao.complete_artifact(job_id, name, "done")
        logger.info("execute_plan: %s completed, result_id=%s", name, result_id)
        return {"subgraph_results": [{"subgraph": name, "result_id": result_id}]}
    except Exception:
        logger.exception("execute_plan: %s failed", name)
        if job_id:
            await dao.complete_artifact(job_id, name, "failed")
        raise
