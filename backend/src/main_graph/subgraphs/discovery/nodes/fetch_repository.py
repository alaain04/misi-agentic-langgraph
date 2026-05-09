"""Node: fetch_repository — git clone and extract package manifest contents.

The cloned temp directory is NOT deleted here. It is passed forward via
repo_path so that trivy_scan can mount it. trivy_scan is responsible for
cleanup; job_runner._finalize() is a safety-net fallback.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_LOCK_FILE_PRIORITY = ("pnpm-lock.yaml", "yarn.lock", "package-lock.json")
_CLONE_TIMEOUT = 120


async def fetch_repository(state: DiscoveryState) -> dict:
    repo_url = state.get("repo_url", "").strip()

    if not repo_url:
        return {"discovery_error": "No repository URL provided"}

    tmp_dir = tempfile.mkdtemp(prefix="misi_repo_")
    logger.info("fetch_repository: cloning %s into %s", repo_url, tmp_dir)

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth=1",
            "--single-branch",
            repo_url,
            tmp_dir,
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

        repo_root = Path(tmp_dir)

        try:
            package_json_content = (repo_root / "package.json").read_text(encoding="utf-8")
        except FileNotFoundError:
            return {
                "discovery_error": "No package.json found in repository root",
                "repo_path": tmp_dir,
            }

        lock_file_content = ""
        lock_file_name = ""
        for name in _LOCK_FILE_PRIORITY:
            lock_path = repo_root / name
            if lock_path.exists():
                lock_file_content = lock_path.read_text(encoding="utf-8")
                lock_file_name = name
                logger.info("fetch_repository: found lock file %s", name)
                break

        logger.info(
            "fetch_repository: cloned %s — package.json found, lock_file=%s",
            repo_url,
            lock_file_name or "none",
        )
        return {
            "package_json_content": package_json_content,
            "lock_file_content": lock_file_content,
            "lock_file_name": lock_file_name,
            "repo_path": tmp_dir,
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("fetch_repository: unexpected error for %s", repo_url)
        return {"discovery_error": f"Repository fetch failed: {exc}"}
