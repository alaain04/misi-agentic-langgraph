"""Execute plan business logic."""

import logging

from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_REGISTRY

logger = logging.getLogger(__name__)


async def execute_plan_service(
    state: MainState,
    dao: JobRepositoryPort,
    ingestion_daos: dict[str, IngestionResultPort],
    config_dict: dict,
) -> dict:
    name = state.get("subgraph_name", "")
    dep_name: str | None = state.get("dep_name")
    job_id = state.get("job_id", "")

    subgraph = SUBGRAPH_REGISTRY.get(name)

    if subgraph is None:
        logger.warning("execute_plan: unknown subgraph %r", name)
        if job_id:
            await dao.complete_artifact(job_id, name, "failed")
        return {"subgraph_results": [{"subgraph": name, "dep_name": dep_name, "error": "unknown subgraph"}]}

    artifact_key = f"{name}:{dep_name}" if dep_name else name
    if job_id:
        await dao.start_artifact(job_id, artifact_key)

    try:
        hydrated_upstream = {}
        for sg, result_id in state.get("upstream_results", {}).items():
            output_dao = ingestion_daos.get(sg)
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
        if dep_name:
            invocation["dependency_name"] = dep_name

        from langchain_core.runnables import RunnableConfig
        subgraph_config: RunnableConfig = {"configurable": {
            **config_dict,
            "thread_id": f"{job_id}:{name}:{dep_name or 'all'}",
        }}
        result = await subgraph.ainvoke(invocation, subgraph_config)

        result_id = result.get("result_id")
        if job_id:
            await dao.update_artifact_data(job_id, artifact_key, {"result_id": result_id})
            await dao.complete_artifact(job_id, artifact_key, "done")
        logger.info("execute_plan: %s(%s) completed, result_id=%s", name, dep_name, result_id)
        return {"subgraph_results": [{"subgraph": name, "dep_name": dep_name, "result_id": result_id}]}
    except Exception:
        logger.exception("execute_plan: %s(%s) failed", name, dep_name)
        if job_id:
            await dao.complete_artifact(job_id, artifact_key, "failed")
        raise
