"""Node: clone_repo — shallow-clone the repository into a temp directory."""

import logging
import os

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_GIT_IMAGE = "alpine/git"


async def clone_repo(state: DiscoveryState, config: RunnableConfig) -> dict:
    """Shallow-clone the repository. Sets repo_path; sets discovery_error on failure."""
    svc = get_services(config)
    container: ContainerRunPort = svc["container"]

    job_id = state["job_id"]
    repo_url = state["repo_url"]
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)

    rc, _out, stderr = await container.run(
        image=_GIT_IMAGE,
        command=f"git clone --depth=1 --single-branch {repo_url} /workspace",
        volume=f"{tmp_dir}:/workspace",
        run_as_root=True,
    )

    if rc != 0:
        logger.error("clone_repo: failed rc=%d stderr=%s", rc, stderr[:300])
        return {
            "repo_path": tmp_dir,
            "discovery_error": stderr.strip() or "git clone failed",
        }

    logger.info("clone_repo: success repo_url=%s", repo_url)

    sha_rc, sha_out, _sha_err = await container.run(
        image=_GIT_IMAGE,
        command="cd /workspace && git rev-parse HEAD",
        volume=f"{tmp_dir}:/workspace",
        run_as_root=True,
    )
    commit_sha = sha_out.strip() if sha_rc == 0 else ""
    return {"repo_path": tmp_dir, "commit_sha": commit_sha}
