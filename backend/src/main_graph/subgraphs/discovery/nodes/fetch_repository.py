"""Node: fetch_repository — git clone into a temp directory."""

import asyncio
import logging
import tempfile

from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 120


async def fetch_repository(state: DiscoveryState) -> dict:
    repo_url = state.get("repo_url", "").strip()

    if not repo_url:
        return {"discovery_error": "No repository URL provided"}

    tmp_dir = tempfile.mkdtemp(prefix="misi_repo_")
    logger.info("fetch_repository: cloning %s into %s", repo_url, tmp_dir)

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", "--single-branch",
            repo_url, tmp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_CLONE_TIMEOUT
            )
        except TimeoutError:
            proc.kill()
            return {"discovery_error": f"git clone timed out after {_CLONE_TIMEOUT}s"}

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()[:300]
            logger.error("fetch_repository: git clone failed: %s", err_msg)
            return {"discovery_error": f"git clone failed: {err_msg}"}

        logger.info("fetch_repository: cloned %s into %s", repo_url, tmp_dir)
        return {"repo_path": tmp_dir}

    except Exception as exc:  # noqa: BLE001
        logger.exception("fetch_repository: unexpected error for %s", repo_url)
        return {"discovery_error": f"Repository fetch failed: {exc}"}
