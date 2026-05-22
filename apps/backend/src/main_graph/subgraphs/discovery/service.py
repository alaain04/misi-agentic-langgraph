"""Discovery subgraph — pure business logic for infrastructure-touching nodes."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.discovery.models import SbomEntry
from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_MIN_NODE_VERSION = 20
_MANIFESTS = ("package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json")


def _create_tmp_dir(job_id: str) -> str:
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)
    return tmp_dir


def _node_version(image: str) -> int | None:
    """Return the numeric Node.js major from an image tag, or None for lts/current."""
    match = re.match(r"node:(\d+)", image)
    return int(match.group(1)) if match else None


def _detect_manifest_files(repo_path: str) -> list[str]:
    root = Path(repo_path)
    return [name for name in _MANIFESTS if (root / name).exists()]


async def clone_repository_service(state: DiscoveryState, container: ContainerRunPort) -> dict:
    repo_url = state.get("repo_url", "").strip()
    if not repo_url:
        return {"discovery_error": "No repository URL provided"}

    tmp_dir = _create_tmp_dir(state["job_id"])
    image = "alpine/git"
    volume = f"{tmp_dir}:/workspace"
    command = f"git clone --depth=1 --single-branch {repo_url} /workspace"
    logger.info("clone_repository: cloning %s into %s", repo_url, tmp_dir)

    returncode, _stdout, stderr = await container.run(image, command, volume)
    if returncode != 0:
        logger.error("clone_repository: clone failed: %s", stderr[:300])
        return {"discovery_error": f"git clone failed: {stderr[:300]}"}

    logger.info("clone_repository: cloned %s", repo_url)
    return {"repo_path": tmp_dir}


async def _run_sbom(
    pm: str, docker_image: str, repo_path: str, container: ContainerRunPort
) -> tuple[dict, str | None]:
    """Run `{pm} sbom --package-lock-only` and return (sbom_data, error)."""
    version = _node_version(docker_image)
    if version is not None and version < _MIN_NODE_VERSION:
        return (
            {},
            f"Node.js {version} does not support '{pm} sbom'"
            f" (requires node:{_MIN_NODE_VERSION}+)",
        )

    command = f"{pm} sbom --sbom-format=cyclonedx --package-lock-only"
    logger.info("generate_sbom: running '%s' in %s", command, docker_image)

    volume = f"{repo_path}:/workspace"
    returncode, stdout, stderr = await container.run(docker_image, command, volume)

    if returncode != 0:
        return {}, stderr or "sbom command failed with no stderr"

    try:
        return json.loads(stdout), None
    except json.JSONDecodeError as exc:
        return {}, f"sbom output is not valid JSON: {exc}"


async def generate_sbom_service(
    state: DiscoveryState,
    container: ContainerRunPort,
    sbom_dao: IngestionResultPort,
) -> dict:
    repo_path = state.get("repo_path", "")
    repo_url = state.get("repo_url", "")

    if not repo_path:
        logger.error("generate_sbom: no repo_path in state")
        entry = SbomEntry(repo_url=repo_url, scan_error="repo_path not available")
        result_id = await sbom_dao.save(entry)
        return {
            "sbom_cyclonedx": {},
            "sbom_result_id": result_id,
            "manifest_files": [],
            "sbom_error": "repo_path not available",
        }

    pm = state.get("detected_package_manager", "npm")
    docker_image = state.get("docker_image", "node:lts-alpine")

    sbom_data, sbom_error = await _run_sbom(pm, docker_image, repo_path, container)
    if sbom_error:
        logger.error("generate_sbom: %s", sbom_error)

    manifest_files = _detect_manifest_files(repo_path)
    entry = SbomEntry(repo_url=repo_url, sbom_cyclonedx=sbom_data, scan_error=sbom_error)
    result_id = await sbom_dao.save(entry)
    logger.info("generate_sbom: saved — result_id=%s error=%s", result_id, sbom_error)

    result: dict = {
        "sbom_cyclonedx": sbom_data,
        "sbom_result_id": result_id,
        "manifest_files": manifest_files,
    }
    if sbom_error:
        result["sbom_error"] = sbom_error
    return result
