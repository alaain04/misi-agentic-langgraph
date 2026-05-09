"""Node: generate_sbom — run {pm} sbom in Docker and persist the CycloneDX result."""

import json
import logging
import re
from pathlib import Path

from src.main_graph.subgraphs.discovery.dao import sbom_dao
from src.main_graph.subgraphs.discovery.models import SbomEntry
from src.main_graph.subgraphs.discovery.nodes.lock_generator_agent import run_docker_command
from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_MIN_NODE_VERSION = 20
_MANIFESTS = ("package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json")


def _node_version(image: str) -> int | None:
    """Return the numeric Node.js major from an image tag, or None for lts/current."""
    match = re.match(r"node:(\d+)", image)
    return int(match.group(1)) if match else None


def _detect_manifest_files(repo_path: str) -> list[str]:
    root = Path(repo_path)
    return [name for name in _MANIFESTS if (root / name).exists()]


async def _run_sbom(pm: str, docker_image: str, repo_path: str) -> tuple[dict, str | None]:
    """Run {pm} sbom --package-lock-only and return (sbom_data, error). error is None on success."""
    version = _node_version(docker_image)
    if version is not None and version < _MIN_NODE_VERSION:
        return {}, f"Node.js {version} does not support '{pm} sbom' (requires node:{_MIN_NODE_VERSION}+)"

    command = f"{pm} sbom --sbom-format=cyclonedx --package-lock-only"
    logger.info("generate_sbom: running '%s' in %s", command, docker_image)

    raw = await run_docker_command.ainvoke({
        "image": docker_image,
        "command": command,
        "workspace": repo_path,
    })
    output = json.loads(raw)

    if output["returncode"] != 0:
        return {}, output["stderr"] or "sbom command failed with no stderr"

    try:
        return json.loads(output["stdout"]), None
    except json.JSONDecodeError as exc:
        return {}, f"sbom output is not valid JSON: {exc}"


async def generate_sbom(state: DiscoveryState) -> dict:
    repo_path = state.get("repo_path", "")
    repo_url = state.get("repo_url", "")

    if not repo_path:
        logger.error("generate_sbom: no repo_path in state")
        entry = SbomEntry(repo_url=repo_url, scan_error="repo_path not available")
        result_id = await sbom_dao.save(entry)
        return {"sbom_cyclonedx": {}, "sbom_result_id": result_id, "manifest_files": [], "sbom_error": "repo_path not available"}

    pm = state.get("detected_package_manager", "npm")
    docker_image = state.get("docker_image", "node:lts-alpine")

    sbom_data, sbom_error = await _run_sbom(pm, docker_image, repo_path)
    if sbom_error:
        logger.error("generate_sbom: %s", sbom_error)

    manifest_files = _detect_manifest_files(repo_path)
    entry = SbomEntry(repo_url=repo_url, sbom_cyclonedx=sbom_data, scan_error=sbom_error)
    result_id = await sbom_dao.save(entry)
    logger.info("generate_sbom: saved — result_id=%s error=%s", result_id, sbom_error)

    result: dict = {"sbom_cyclonedx": sbom_data, "sbom_result_id": result_id, "manifest_files": manifest_files}
    if sbom_error:
        result["sbom_error"] = sbom_error
    return result
