"""Node: install_deps — install dependencies when no lock file is present."""

import logging
import os

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_LOCK_FILE_NAMES = {
    "pnpm": "pnpm-lock.yaml",
    "yarn": "yarn.lock",
    "npm": "package-lock.json",
}


def _install_command(pm: str, pm_version: str) -> str:
    if pm == "pnpm":
        return (
            f"cd /workspace && NO_UPDATE_NOTIFIER=1 npm install -g "
            f"pnpm@{pm_version} && pnpm install  --ignore-scripts"
        )
    return "cd /workspace && NO_UPDATE_NOTIFIER=1 npm install --ignore-scripts"


def _lockfile_only_command(pm: str, pm_version: str) -> str | None:
    if pm == "npm":
        return (
            "cd /workspace && NO_UPDATE_NOTIFIER=1 npm install "
            "--package-lock-only --ignore-scripts"
        )
    if pm == "pnpm":
        return (
            f"cd /workspace && NO_UPDATE_NOTIFIER=1 npm install -g "
            f"pnpm@{pm_version} && pnpm install --lockfile-only"
        )
    return None


async def _run_with_peer_retry(
    container: ContainerRunPort, image: str, volume: str, cmd: str, pm: str
) -> tuple[int, str, str]:
    rc, out, err = await container.run(
        image=image, command=cmd, volume=volume, run_as_root=True
    )
    if rc == 0 or pm == "pnpm":
        return rc, out, err
    if "ERESOLVE" in err or "ESBOMPROBLEMS" in err or "peer" in err.lower():
        rc, out, err = await container.run(
            image=image,
            command=cmd + " --legacy-peer-deps",
            volume=volume,
            run_as_root=True,
        )
    if rc != 0 and ("ERESOLVE" in err or "peer" in err.lower()):
        rc, out, err = await container.run(
            image=image, command=cmd + " --force", volume=volume, run_as_root=True
        )
    return rc, out, err


async def install_deps(state: DiscoveryState, config: RunnableConfig) -> None:
    """Install dependencies and update has_lock_file based on what was created."""
    svc = get_services(config)
    container: ContainerRunPort = svc["container"]

    repo_path = state.get("repo_path")
    pm = state.get("package_manager")
    pm_version = state.get("package_manager_version")
    docker_image = state.get("docker_node_image")
    volume = f"{repo_path}:/workspace"

    cmd = _install_command(pm, pm_version)
    rc, _out, err = await _run_with_peer_retry(container, docker_image, volume, cmd, pm)

    if rc != 0:
        logger.warning("install_deps: install failed rc=%d err=%s", rc, err[:300])

    lock_file = _LOCK_FILE_NAMES.get(pm)
    lock_created = os.path.exists(os.path.join(repo_path, lock_file))

    if not lock_created:
        fallback_cmd = _lockfile_only_command(pm, pm_version)
        if fallback_cmd is not None:
            rc2, _out2, err2 = await container.run(
                image=docker_image,
                command=fallback_cmd,
                volume=volume,
                run_as_root=True,
            )
            if rc2 != 0:
                logger.warning(
                    "install_deps: lockfile-only fallback failed rc=%d err=%s",
                    rc2,
                    err2[:300],
                )
            lock_created = os.path.exists(os.path.join(repo_path, lock_file))

    logger.info("install_deps: lock_created=%s", lock_created)
