from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.state import DiscoveryState, ProjectMetadata
from src.models.results import PrepResult
from src.utils.config import settings
from src.utils.dependency_graph import (
    build_dependency_graph,
    count_dependencies,
    read_package_json,
)

logger = logging.getLogger(__name__)


async def save_prep_result(state: DiscoveryState, config: RunnableConfig) -> dict:
    if state.get("discovery_error"):
        logger.info("save_prep_result: skipping due to discovery_error")
        return {}

    svc = get_services(config)
    dao = svc["result_dao"]
    cache = svc.get("input_cache")
    pm = state.get("package_manager") or "unknown"
    repo_path = state.get("repo_path", "")
    repo_url = state.get("repo_url", "")
    commit_sha = state.get("commit_sha") or ""
    docker_image = state.get("docker_node_image") or "node:lts-alpine"

    pkg = read_package_json(repo_path)
    dep_graph = await build_dependency_graph(
        repo_path,
        pm,
        container=svc["container"],
        docker_image=settings.trivy_image,
        pkg=pkg,
        cache=cache,
        repo_url=repo_url,
        commit_sha=commit_sha,
    )
    direct, transitive = count_dependencies(dep_graph)
    metadata = ProjectMetadata(
        name=pkg.get("name", "unknown"),
        package_manager=pm,
        direct_dependencies_count=direct,
        transitive_dependencies_count=transitive,
    )

    result = PrepResult(
        job_id=state["job_id"],
        repo_path=repo_path,
        project_metadata=dict(metadata),
        manifest_files=state.get("manifest_files") or [],
        package_manager=pm,
        docker_image=docker_image,
        repo_url=repo_url,
        commit_sha=commit_sha,
        dependency_graph=dep_graph,
    )
    prep_result_id = await dao.save_prep(result)
    logger.info("save_prep_result: saved prep_result_id=%s", prep_result_id)
    return {"prep_result_id": prep_result_id}
