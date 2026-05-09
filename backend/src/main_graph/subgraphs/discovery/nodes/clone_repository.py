"""Node: clone_repository — git clone via Docker."""

import asyncio
import logging
import os

from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 120


async def _docker_run(args: list[str], timeout: int) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, f"timed out after {timeout}s"
    return proc.returncode, stderr_bytes.decode(errors="replace").strip()


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
    user = f"{os.getuid()}:{os.getgid()}"

    logger.info("clone_repository: cloning %s into %s", repo_url, tmp_dir)

    returncode, stderr = await _docker_run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            user,
            "-v",
            volume,
            "alpine/git",
            "clone",
            "--depth=1",
            "--single-branch",
            repo_url,
            "/workspace",
        ],
        timeout=_CLONE_TIMEOUT,
    )
    if returncode != 0:
        logger.error("clone_repository: clone failed: %s", stderr[:300])
        return {"discovery_error": f"git clone failed: {stderr[:300]}"}

    logger.info("clone_repository: cloned %s", repo_url)
    return {"repo_path": tmp_dir}
