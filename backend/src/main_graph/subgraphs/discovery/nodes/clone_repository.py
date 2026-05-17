"""Node: clone_repository — git clone via Docker."""

import logging
import os

from backend.src.main_graph.subgraphs.discovery.tools.docker import run_docker_command
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
    volume = f"{tmp_dir}:/workspace"
    image = "alpine/git"
    cmd = f"git clone --depth=1 --single-branch {repo_url} /workspace"
    logger.info("clone_repository: cloning %s into %s", repo_url, tmp_dir)

    result = await run_docker_command(image, volume, cmd)
    returncode = result["returncode"]
    stderr = result["stderr"]
    
    if returncode != 0:
        logger.error("clone_repository: clone failed: %s", stderr[:300])
        return {"discovery_error": f"git clone failed: {stderr[:300]}"}

    logger.info("clone_repository: cloned %s", repo_url)
    return {"repo_path": tmp_dir}
