"""Node: generate_sbom — run package manager SBOM command and persist result."""

import json
import logging

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.models import SbomEntry
from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_FALLBACK_IMAGES = ("node:22-alpine", "node:20-alpine")


def _sbom_command(pm: str, pm_version: str, has_lock: bool) -> str:
    if pm == "pnpm":
        return (
            f"cd /workspace && NO_UPDATE_NOTIFIER=1 npm install -g pnpm@{pm_version}"
            " && pnpm sbom --sbom-format=cyclonedx --package-lock-only"
        )
    if pm == "yarn" and has_lock:
        return (
            "cd /workspace && NO_UPDATE_NOTIFIER=1 npm install --package-lock-only --ignore-scripts"
            " && NO_UPDATE_NOTIFIER=1 npm sbom --sbom-format=cyclonedx --package-lock-only"
        )
    if has_lock:
        return "cd /workspace && NO_UPDATE_NOTIFIER=1 npm sbom --sbom-format=cyclonedx --package-lock-only"
    return "cd /workspace && NO_UPDATE_NOTIFIER=1 npm sbom --sbom-format=cyclonedx"


async def _try_sbom(
    container: ContainerRunPort, image: str, volume: str, cmd: str, pm: str
) -> tuple[int, str, str]:
    rc, out, err = await container.run(image=image, command=cmd, volume=volume, run_as_root=True)
    if rc == 0 or pm == "pnpm":
        return rc, out, err
    if "ERESOLVE" in err or "ESBOMPROBLEMS" in err or "peer" in err.lower():
        rc, out, err = await container.run(
            image=image, command=cmd + " --legacy-peer-deps", volume=volume, run_as_root=True
        )
    if rc != 0 and ("ERESOLVE" in err or "peer" in err.lower()):
        rc, out, err = await container.run(
            image=image, command=cmd + " --force", volume=volume, run_as_root=True
        )
    return rc, out, err


async def generate_sbom(state: DiscoveryState, config: RunnableConfig) -> dict:
    """Run SBOM command; persist and return sbom_cyclonedx."""
    svc = get_services(config)
    container: ContainerRunPort = svc["container"]
    sbom_dao = svc["sbom_dao"]

    repo_url = state["repo_url"]
    repo_path = state["repo_path"]
    pm = state.get("detected_package_manager", "npm")
    pm_version = state.get("package_manager_version", "latest")
    docker_image = state.get("docker_image", "node:lts-alpine")
    has_lock = state.get("has_lock_file", False)

    volume = f"{repo_path}:/workspace"
    cmd = _sbom_command(pm, pm_version, has_lock)

    # Try primary image; fall back to higher Node versions on engine errors
    images = [docker_image] + [img for img in _FALLBACK_IMAGES if img != docker_image]
    rc, out, err = 1, "", "never attempted"
    for image in images:
        rc, out, err = await _try_sbom(container, image, volume, cmd, pm)
        if rc == 0:
            break
        if "requires Node" not in err and "unsupported engine" not in err.lower():
            break  # not a node-version issue — further images won't help

    if rc != 0:
        logger.error("generate_sbom: all attempts failed err=%s", err[:300])
        entry = SbomEntry(repo_url=repo_url, scan_error=err[:300])
        result_id = await sbom_dao.save(entry)
        return {"sbom_cyclonedx": {}, "sbom_result_id": result_id, "sbom_error": err[:300]}

    try:
        sbom = json.loads(out)
    except json.JSONDecodeError as exc:
        msg = f"JSON parse error: {exc}"
        logger.error("generate_sbom: %s", msg)
        entry = SbomEntry(repo_url=repo_url, scan_error=msg)
        result_id = await sbom_dao.save(entry)
        return {"sbom_cyclonedx": {}, "sbom_result_id": result_id, "sbom_error": msg}

    entry = SbomEntry(repo_url=repo_url, sbom_cyclonedx=sbom)
    result_id = await sbom_dao.save(entry)
    logger.info("generate_sbom: success pm=%s components=%d", pm, len(sbom.get("components", [])))
    return {"sbom_cyclonedx": sbom, "sbom_result_id": result_id}
