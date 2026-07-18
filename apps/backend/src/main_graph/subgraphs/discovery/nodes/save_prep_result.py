from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.dependency_graph import build_dependency_graph
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.models.results import PrepResult

logger = logging.getLogger(__name__)


async def save_prep_result(state: DiscoveryState, config: RunnableConfig) -> dict:
    if state.get("discovery_error"):
        logger.info("save_prep_result: skipping due to discovery_error")
        return {}

    dao = get_services(config)["result_dao"]
    pm = state.get("detected_package_manager") or "unknown"
    result = PrepResult(
        job_id=state["job_id"],
        repo_path=state.get("repo_path", ""),
        project_metadata=dict(state.get("project_metadata") or {}),
        manifest_files=state.get("manifest_files") or [],
        detected_package_manager=pm,
        docker_image=state.get("docker_image") or "node:lts-alpine",
        dependency_graph=build_dependency_graph(state.get("repo_path", ""), pm),
        discovery_summary=state.get("project_context") or "",
        vector_store_id=state.get("vector_store_id") or "",
    )
    prep_result_id = await dao.save_prep(result)
    logger.info("save_prep_result: saved prep_result_id=%s", prep_result_id)
    return {"prep_result_id": prep_result_id}
