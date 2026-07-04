"""Node: inspect_repo — read manifests to determine package manager and Docker image."""

import json
import logging
import os
import re

from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_LOCK_FILES = [
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
]
_NODE_VERSIONS = [18, 20, 22, 24]
_PNPM_V11_MIN_NODE = 22


def _min_node_from_engines(engines_node: str) -> int:
    match = re.search(r"(\d+)", engines_node or "")
    return int(match.group(1)) if match else 0


def _select_node_image(min_node: int, pm: str, pm_version: str) -> str:
    if pm == "pnpm":
        try:
            major = int(pm_version.split(".")[0])
        except (ValueError, AttributeError):
            major = 0
        if major >= 11:
            min_node = max(min_node, _PNPM_V11_MIN_NODE)
    for v in _NODE_VERSIONS:
        if v >= min_node:
            return f"node:{v}-alpine"
    return "node:lts-alpine"


def inspect_repo(state: DiscoveryState) -> dict:
    """Read package.json and lock files to determine package manager, version, and Docker image."""
    repo_path = state.get("repo_path", "")

    pkg: dict = {}
    pkg_path = os.path.join(repo_path, "package.json")
    if os.path.exists(pkg_path):
        try:
            with open(pkg_path) as f:
                pkg = json.load(f)
        except Exception:
            pass

    manifest_files = ["package.json"] if os.path.exists(pkg_path) else []
    detected_pm = "npm"
    has_lock_file = False
    for lock_file, pm in _LOCK_FILES:
        if os.path.exists(os.path.join(repo_path, lock_file)):
            detected_pm = pm
            has_lock_file = True
            manifest_files.append(lock_file)
            break

    # Parse packageManager field: e.g. "pnpm@9.15.0+sha512..."
    pm_version = "latest"
    pm_field = pkg.get("packageManager", "")
    if pm_field and "@" in pm_field:
        _name, ver = pm_field.split("@", 1)
        pm_version = ver.split("+")[0]
        if not has_lock_file:
            detected_pm = _name

    min_node = _min_node_from_engines(pkg.get("engines", {}).get("node", ""))
    docker_image = _select_node_image(min_node, detected_pm, pm_version)

    logger.info(
        "inspect_repo: pm=%s version=%s has_lock=%s image=%s",
        detected_pm, pm_version, has_lock_file, docker_image,
    )

    return {
        "manifest_files": manifest_files,
        "detected_package_manager": detected_pm,
        "package_manager_version": pm_version,
        "has_lock_file": has_lock_file,
        "docker_image": docker_image,
    }
