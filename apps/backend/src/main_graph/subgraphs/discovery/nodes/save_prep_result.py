from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.db.input_cache import cache_key, get_or_compute
from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.dependency_graph import build_dependency_graph
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.models.results import PrepResult

logger = logging.getLogger(__name__)


async def save_prep_result(state: DiscoveryState, config: RunnableConfig) -> dict:
    if state.get("discovery_error"):
        logger.info("save_prep_result: skipping due to discovery_error")
        return {}

    svc = get_services(config)
    dao = svc["result_dao"]
    cache = svc.get("input_cache")
    pm = state.get("detected_package_manager") or "unknown"
    repo_path = state.get("repo_path", "")
    repo_url = state.get("repo_url", "")
    commit_sha = state.get("commit_sha") or ""

    async def _build_graph() -> dict:
        return build_dependency_graph(repo_path, pm)

    # The dependency graph is a pure function of the committed source, so it is
    # cached indefinitely (keyed by commit sha); a cache miss/error recomputes.
    if cache is not None and commit_sha:
        dep_graph = await get_or_compute(
            cache, cache_key(repo_url, commit_sha, pm, "dependency_graph"), _build_graph
        )
    else:
        dep_graph = await _build_graph()

    result = PrepResult(
        job_id=state["job_id"],
        repo_path=repo_path,
        project_metadata=dict(state.get("project_metadata") or {}),
        manifest_files=state.get("manifest_files") or [],
        detected_package_manager=pm,
        docker_image=state.get("docker_image") or "node:lts-alpine",
        repo_url=repo_url,
        commit_sha=commit_sha,
        dependency_graph=dep_graph,
        discovery_summary=state.get("project_context") or "",
        vector_store_id=state.get("vector_store_id") or "",
        codegraph_ready=state.get("codegraph_ready") or False,
    )
    prep_result_id = await dao.save_prep(result)
    logger.info("save_prep_result: saved prep_result_id=%s", prep_result_id)
    return {"prep_result_id": prep_result_id}
