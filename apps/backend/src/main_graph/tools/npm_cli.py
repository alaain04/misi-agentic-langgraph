"""npm tools executed inside a sandboxed container: npm_list, npm_audit, npm_outdated."""
from __future__ import annotations

import json
import logging
import os

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.tools.registry import register

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE = "node:lts-alpine"


async def _run_npm(
    args: list[str], repo_path: str, container: ContainerRunPort, docker_image: str
) -> tuple[str, str]:
    command = "cd /workspace && npm " + " ".join(args)
    volume = f"{repo_path}:/workspace"
    _rc, stdout, stderr = await container.run(
        image=docker_image, command=command, volume=volume, run_as_root=True
    )
    return stdout, stderr


def _safe_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


@register("npm_list", "Runs `npm list --json`; returns full dependency tree with installed versions")
async def npm_list(repo_path: str, container: ContainerRunPort, docker_image: str = _DEFAULT_IMAGE) -> dict:
    try:
        stdout, _ = await _run_npm(["list", "--json", "--all"], repo_path, container, docker_image)
        return _safe_json(stdout)
    except Exception as exc:
        logger.warning("npm_list failed: %s", exc)
        return {"error": str(exc)}


@register("npm_audit", "Runs `npm audit --json`; returns vulnerabilities, severities, and affected packages")
async def npm_audit(repo_path: str, container: ContainerRunPort, docker_image: str = _DEFAULT_IMAGE) -> dict:
    try:
        stdout, _ = await _run_npm(["audit", "--json"], repo_path, container, docker_image)
        return _safe_json(stdout)
    except Exception as exc:
        logger.warning("npm_audit failed: %s", exc)
        return {"error": str(exc)}


@register("npm_outdated", "Returns packages with newer versions available via `npm outdated --json`")
async def npm_outdated(repo_path: str, container: ContainerRunPort, docker_image: str = _DEFAULT_IMAGE) -> dict:
    try:
        stdout, _ = await _run_npm(["outdated", "--json"], repo_path, container, docker_image)
        data = _safe_json(stdout)
        return {"outdated": data}
    except Exception as exc:
        logger.warning("npm_outdated failed: %s", exc)
        return {"error": str(exc)}


def _in_subtree(deps: dict, target: str) -> bool:
    """Return True if target package exists anywhere in the deps subtree."""
    if target in deps:
        return True
    return any(_in_subtree(info.get("dependencies") or {}, target) for info in deps.values())


def _find_chain(deps: dict, target: str, prefix: str = "") -> str:
    """Return first dep_chain string that reaches target, or 'unknown'."""
    for name, info in deps.items():
        current = f"{prefix} → {name}" if prefix else name
        sub = info.get("dependencies") or {}
        if target in sub:
            return f"{current} → {target}"
        result = _find_chain(sub, target, current)
        if result != "unknown":
            return result
    return "unknown"


@register(
    "resolve_transitive_parent",
    "Determines if a package is a direct or transitive dependency and identifies which direct deps bring it in",
)
async def resolve_transitive_parent(
    repo_path: str, package_name: str, container: ContainerRunPort, docker_image: str = _DEFAULT_IMAGE
) -> dict:
    try:
        pkg_path = os.path.join(repo_path, "package.json")
        with open(pkg_path) as f:
            pkg = json.load(f)
        direct_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

        if package_name in direct_deps:
            return {
                "package": package_name,
                "is_direct": True,
                "brought_in_by": [],
                "dep_chain": package_name,
            }

        stdout, _ = await _run_npm(["ls", "--json", "--all"], repo_path, container, docker_image)
        tree = _safe_json(stdout)
        tree_deps = tree.get("dependencies") or {}

        parents = [name for name, info in tree_deps.items()
                   if _in_subtree(info.get("dependencies") or {}, package_name)]

        return {
            "package": package_name,
            "is_direct": False,
            "brought_in_by": parents,
            "dep_chain": _find_chain(tree_deps, package_name),
        }
    except Exception as exc:
        logger.warning("resolve_transitive_parent failed: %s", exc)
        return {"error": str(exc), "package": package_name}
