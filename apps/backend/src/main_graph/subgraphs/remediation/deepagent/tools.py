from __future__ import annotations

import asyncio
import json
import logging
import re

from langchain_core.tools import tool

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.discovery.dependency_graph import dependents_of
from src.main_graph.subgraphs.remediation.verify import verify_working_copy
from src.main_graph.subgraphs.remediation.workspace import apply_bump

logger = logging.getLogger(__name__)

_GITHUB_REPO_RE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?\s*$")


async def _resolve_github_repo(
    package_name: str, repo_path: str, container: ContainerRunPort, docker_image: str
) -> tuple[str, str] | None:
    command = f"cd /workspace && npm view {package_name} repository.url"
    rc, stdout, _stderr = await container.run(
        image=docker_image,
        command=command,
        volume=f"{repo_path}:/workspace",
        run_as_root=True,
    )
    if rc != 0:
        return None
    match = _GITHUB_REPO_RE.search(stdout.strip())
    return (match.group(1), match.group(2)) if match else None


def make_read_release_notes_tool(
    repo_path: str, container: ContainerRunPort, docker_image: str
):
    @tool
    async def read_release_notes(package_name: str) -> dict:
        """Fetch recent GitHub release notes for an npm package, resolved
        via its registry-declared repository URL. Use this to check for
        breaking changes between the installed version and a candidate
        upgrade before deciding whether a bump is safe, or whether code
        needs to change too."""
        resolved = await _resolve_github_repo(
            package_name, repo_path, container, docker_image
        )
        if resolved is None:
            return {
                "package_name": package_name,
                "available": False,
                "error": "could not resolve a GitHub repository for this package",
            }
        owner, repo = resolved
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "api", f"repos/{owner}/{repo}/releases", "--paginate",
                "-q", ".[:20]",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
        except FileNotFoundError:
            return {
                "package_name": package_name,
                "available": False,
                "error": "gh CLI not found",
            }
        if proc.returncode != 0:
            return {
                "package_name": package_name,
                "available": False,
                "error": err.decode(errors="replace")[:300],
            }
        try:
            releases = json.loads(out.decode(errors="replace") or "[]")
        except json.JSONDecodeError:
            return {
                "package_name": package_name,
                "available": False,
                "error": "unparseable gh output",
            }
        return {
            "package_name": package_name,
            "available": True,
            "repository": f"{owner}/{repo}",
            "releases": [
                {
                    "tag": release.get("tag_name"),
                    "name": release.get("name"),
                    "body": (release.get("body") or "")[:2000],
                }
                for release in releases
            ],
        }

    return read_release_notes


def make_dependents_of_tool(dependency_graph: dict):
    @tool
    def dependents_of_tool(package_name: str) -> list[str]:
        """Return every package in this project's dependency tree that
        depends on `package_name`, whether or not it has a flagged finding.
        Structural only - does not confirm a declared version range still
        holds after a bump; call `verify` for that."""
        return dependents_of(dependency_graph, package_name)

    return dependents_of_tool


def make_bump_dependency_tool(work_dir: str):
    @tool
    def bump_dependency(target_dep: str, to_range: str) -> dict:
        """Edit package.json to set target_dep's declared range to
        to_range. Returns {"applied": false} if target_dep isn't declared
        in dependencies/devDependencies."""
        return {"applied": apply_bump(work_dir, target_dep, to_range)}

    return bump_dependency


def make_verify_tool(
    work_dir: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
    default_targeted_deps: list[str],
):
    @tool
    async def verify(targeted_deps: list[str] | None = None) -> dict:
        """Install, build (if scripted), test (if scripted), and re-audit
        the working copy. Use this to self-correct as you iterate - it is
        a guide for your own next step, not the final verdict: a separate
        deterministic check re-verifies from a clean clone before anything
        ships."""
        result = await verify_working_copy(
            work_dir,
            container,
            docker_image,
            package_manager,
            targeted_deps or default_targeted_deps,
        )
        return result.model_dump()

    return verify
