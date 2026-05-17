"""Node: clone_repository — git clone via Docker."""

import json
import logging
import os

from src.main_graph.subgraphs.discovery.tools.docker import run_docker_command
from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)


def _create_tmp_dir(job_id: str) -> str:
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)
    return tmp_dir


async def clone_repository(state: DiscoveryState) -> dict:
    repo_url = state.get("repo_url", "").strip()
    if not repo_url:
        return {"discovery_error": "No repository URL provided"}

    tmp_dir = _create_tmp_dir(state["job_id"])
    image = "alpine/git"
    volume = f"{tmp_dir}:/workspace"
    cmd = f"git clone --depth=1 --single-branch {repo_url} /workspace"
    logger.info("clone_repository: cloning %s into %s", repo_url, tmp_dir)

    raw = await run_docker_command.ainvoke({"image": image, "volume": volume, "command": cmd})
    result = json.loads(raw)
    returncode = result["returncode"]
    stderr = result["stderr"]

    if returncode != 0:
        logger.error("clone_repository: clone failed: %s", stderr[:300])
        return {"discovery_error": f"git clone failed: {stderr[:300]}"}

    logger.info("clone_repository: cloned %s", repo_url)
    return {"repo_path": tmp_dir}
