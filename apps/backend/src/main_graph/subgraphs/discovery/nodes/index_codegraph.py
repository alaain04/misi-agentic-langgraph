"""Node: index_codegraph — build the CodeGraph blast-radius index once."""

from __future__ import annotations

import logging
import os

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.utils.config import settings

logger = logging.getLogger(__name__)


async def index_codegraph(state: DiscoveryState, config: RunnableConfig) -> dict:
    """Build the CodeGraph index for the cloned repo. Sets codegraph_ready."""
    repo_path = state.get("repo_path", "")
    if not repo_path or not os.path.isdir(repo_path):
        logger.warning("index_codegraph: repo_path missing or not a dir, skipping")
        return {"codegraph_ready": False}

    container: ContainerRunPort = get_services(config)["container"]
    rc, _out, err = await container.run(
        image=settings.codegraph_docker_image,
        command="codegraph init --force /workspace",
        volume=f"{repo_path}:/workspace",
        run_as_root=True,
    )

    if rc != 0:
        logger.warning("index_codegraph: init failed rc=%d err=%s", rc, err[:300])
        return {"codegraph_ready": False}

    logger.info("index_codegraph: success repo_path=%s", repo_path)
    return {"codegraph_ready": True}
