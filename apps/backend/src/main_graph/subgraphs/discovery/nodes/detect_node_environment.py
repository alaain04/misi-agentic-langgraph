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
_DEFAULT_NODE_VERSION = 22


def _min_node_from_engines(engine: str) -> str:
    if not engine:
        return _DEFAULT_NODE_VERSION

    # Prefer an explicit lower bound: >=20, >=20.18.0
    match = re.search(r">=\s*(\d+(?:\.\d+){0,2})", engine)
    if match:
        return match.group(1)

    # ^20 / ~20 / 20.x
    match = re.search(r"(?:\^|~)?\s*(\d+)(?:\.(\d+))?", engine)
    if match:
        major = match.group(1)
        return major

    return _DEFAULT_NODE_VERSION


def detect_node_environment(state: DiscoveryState):
    repo_path = state.get("repo_path")
    pkg_path = os.path.join(repo_path, "package.json")

    pkg = {}
    if os.path.exists(pkg_path):
        try:
            with open(pkg_path, encoding="utf-8") as f:
                pkg = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

    manifest_files = ["package.json"] if pkg else []

    # 1. Detect package manager from lockfile.
    lock_pm = None
    lockfile_generated = ""
    for lock_file, pm in _LOCK_FILES:
        if os.path.exists(os.path.join(repo_path, lock_file)):
            lock_pm = pm
            lockfile_generated = lock_file
            manifest_files.append(lock_file)
            break

    # 2. packageManager has higher priority than lockfile.
    detected_pm = lock_pm or "npm"
    pm_version = "latest"

    package_manager = pkg.get("packageManager")
    if package_manager:
        pm_name, pm_version = _parse_package_manager(package_manager)
        detected_pm = pm_name

    # 3. Minimum Node version from engines.node.
    node_engine = pkg.get("engines", {}).get("node", "")
    min_node = _min_node_from_engines(node_engine)

    return {
        "package_manager": detected_pm,
        "package_manager_version": pm_version,
        "node_version": min_node,
        "docker_node_image": f"node:{min_node}-alpine",
        "lockfile_generated": lockfile_generated,
        "manifest_files": manifest_files,
    }


def _parse_package_manager(value: str):
    """
    Examples:
        npm@10.9.0        -> ("npm", "10.9.0")
        pnpm@9.15.0       -> ("pnpm", "9.15.0")
        yarn@4.5.1        -> ("yarn", "4.5.1")
        pnpm@9.15.0+sha   -> ("pnpm", "9.15.0")
    """
    if "@" not in value:
        return value, "latest"

    name, version = value.split("@", 1)
    version = version.split("+", 1)[0]

    return name, version
