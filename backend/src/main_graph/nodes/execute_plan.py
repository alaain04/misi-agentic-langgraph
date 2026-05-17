"""Execute plan node — dispatches to the appropriate skeleton subgraph by name."""

import logging

from src.api.dependencies import get_job_repo
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import (
    SUBGRAPH_DAOS,
    SUBGRAPH_REGISTRY,
)

logger = logging.getLogger(__name__)

_dao: JobRepositoryPort = get_job_repo()


async def execute_plan(state: MainState) -> dict:
    name = state.get("subgraph_name", "")
    job_id = state.get("job_id", "")

    subgraph = SUBGRAPH_REGISTRY.get(name)

    if subgraph is None:
        logger.warning("execute_plan: unknown subgraph %r", name)
        if job_id:
            await _dao.complete_artifact(job_id, name, "failed")
        return {"subgraph_results": [{"subgraph": name, "error": "unknown subgraph"}]}

    if job_id:
        await _dao.start_artifact(job_id, name)

    try:
        hydrated_upstream = {}
        for sg, result_id in state.get("upstream_results", {}).items():
            output_dao = SUBGRAPH_DAOS.get(sg)
            if output_dao and result_id:
                data = await output_dao.get(result_id)
                if data:
                    hydrated_upstream[sg] = data

        invocation: dict = {
            "sbom_cyclonedx": state.get("sbom_cyclonedx", {}),
            "discovery_summary": state.get("discovery_summary", ""),
            "concern": state.get("concern", ""),
            "upstream_results": hydrated_upstream,
        }
        if repo_path := state.get("repo_path"):
            invocation["repo_path"] = repo_path

        result = await subgraph.ainvoke(invocation)

        result_id = result.get("result_id")
        if job_id:
            await _dao.update_artifact_data(job_id, name, {"result_id": result_id})
            await _dao.complete_artifact(job_id, name, "done")
        logger.info("execute_plan: %s completed, result_id=%s", name, result_id)
        return {"subgraph_results": [{"subgraph": name, "result_id": result_id}]}
    except Exception:
        logger.exception("execute_plan: %s failed", name)
        if job_id:
            await _dao.complete_artifact(job_id, name, "failed")
        raise
