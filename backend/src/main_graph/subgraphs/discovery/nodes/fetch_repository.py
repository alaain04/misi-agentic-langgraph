"""Node: fetch_repository — git clone and dependency install via Docker."""

import asyncio
import logging
import os
from pathlib import Path

from pydantic import BaseModel

from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 120
_INSTALL_TIMEOUT = 300

_llm = get_llm(Model.GPT_4O_MINI)

_MANIFEST_CANDIDATES = [
    "package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json",
    "requirements.txt", "pyproject.toml", "setup.py", "Pipfile",
    "Gemfile", "go.mod", "Cargo.toml", "composer.json",
]


class _InstallPlan(BaseModel):
    image: str
    """Docker image to use, e.g. 'node:lts-alpine'."""
    command: str
    """Shell command to install dependencies in /workspace, e.g. 'npm install'."""


async def _docker_run(args: list[str], timeout: int) -> tuple[int, str]:
    """Run a docker command; return (returncode, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return -1, f"timed out after {timeout}s"
    return proc.returncode, stderr_bytes.decode(errors="replace").strip()


async def _decide_install_plan(repo_path: str) -> _InstallPlan | None:
    """Ask the LLM to choose a Docker image and install command from the manifest files found."""
    found = [f for f in _MANIFEST_CANDIDATES if (Path(repo_path) / f).exists()]
    if not found:
        return None

    prompt = (
        "You detect dependency managers from repository manifest files and decide how to install them in Docker.\n\n"
        f"Manifest files found: {', '.join(found)}\n\n"
        "Return:\n"
        "- image: the Docker image to use (e.g. 'node:alpine', 'python:3.12-slim')\n"
        "- command: the shell command to install dependencies in /workspace "
        "(e.g. 'npm install', 'pip install -r requirements.txt -q')"
    )
    structured = _llm.with_structured_output(_InstallPlan)
    return await structured.ainvoke(prompt)


def _create_tmp_dir(job_id: str) -> str:
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)
    return tmp_dir
    
async def fetch_repository(state: DiscoveryState) -> dict:
    repo_url = state.get("repo_url", "").strip()
    if not repo_url:
        return {"discovery_error": "No repository URL provided"}

    tmp_dir = _create_tmp_dir(state["job_id"])
    volume = f"{tmp_dir}:/workspace"
    user = f"{os.getuid()}:{os.getgid()}"
    
    logger.info("fetch_repository: cloning %s into %s", repo_url, tmp_dir)

    returncode, stderr = await _docker_run(
        [
            "docker", "run", "--rm",
            "--user", user,
            "-v", volume,
            "alpine/git", "clone", "--depth=1", "--single-branch", repo_url, "/workspace",
        ],
        timeout=_CLONE_TIMEOUT,
    )
    if returncode != 0:
        logger.error("fetch_repository: clone failed: %s", stderr[:300])
        return {"discovery_error": f"git clone failed: {stderr[:300]}"}

    logger.info("fetch_repository: cloned %s", repo_url)

    plan = await _decide_install_plan(tmp_dir)
    if plan:
        logger.info("fetch_repository: installing deps — image=%s command=%s", plan.image, plan.command)
        returncode, stderr = await _docker_run(
            ["docker", "run", "--rm", "-v", volume,
             plan.image, "sh", "-c", f"cd /workspace && {plan.command}"],
            timeout=_INSTALL_TIMEOUT,
        )
        if returncode != 0:
            logger.warning("fetch_repository: dep install failed (non-fatal): %s", stderr[:200])
    else:
        logger.info("fetch_repository: no manifest files found, skipping dep install")

    return {"repo_path": tmp_dir}
