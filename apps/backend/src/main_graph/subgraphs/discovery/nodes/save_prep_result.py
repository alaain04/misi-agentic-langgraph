from __future__ import annotations
import json
import logging
import os

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.models.results import PrepResult

logger = logging.getLogger(__name__)


def _build_dependency_graph(repo_path: str) -> dict:
    """Read package.json and return {direct: {pkg: ver}, transitive: {}}."""
    pkg_path = os.path.join(repo_path or "", "package.json")
    try:
        with open(pkg_path) as f:
            pkg = json.load(f)
        direct = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        return {"direct": direct, "transitive": {}}
    except Exception:
        return {"direct": {}, "transitive": {}}


async def save_prep_result(state: DiscoveryState, config: RunnableConfig) -> dict:
    if state.get("discovery_error"):
        logger.info("save_prep_result: skipping due to discovery_error")
        return {}

    dao = get_services(config)["result_dao"]
    result = PrepResult(
        job_id=state["job_id"],
        repo_path=state.get("repo_path", ""),
        project_metadata=dict(state.get("project_metadata") or {}),
        manifest_files=state.get("manifest_files") or [],
        detected_package_manager=state.get("detected_package_manager") or "unknown",
        dependency_graph=_build_dependency_graph(state.get("repo_path", "")),
        sbom_cyclonedx={},
        discovery_summary=state.get("project_context") or "",
        vector_store_id=state.get("vector_store_id") or "",
    )
    prep_result_id = await dao.save_prep(result)
    logger.info("save_prep_result: saved prep_result_id=%s", prep_result_id)
    return {"prep_result_id": prep_result_id}
