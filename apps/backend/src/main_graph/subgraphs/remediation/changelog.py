"""Shared GitHub release-notes fetch for an npm package -- used by the
per-target remediation subagent's read_release_notes tool
(deepagent/tools.py) and by the tier classifier (classify.py)."""

from __future__ import annotations

import asyncio
import json
import re
import shlex

from src.domain.ports.container_run_port import ContainerRunPort

_GITHUB_REPO_RE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?\s*$")


async def _resolve_github_repo(
    package_name: str, repo_path: str, container: ContainerRunPort, docker_image: str
) -> tuple[str, str] | None:
    command = f"cd /workspace && npm view {shlex.quote(package_name)} repository.url"
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


async def fetch_release_notes(
    package_name: str, repo_path: str, container: ContainerRunPort, docker_image: str
) -> dict:
    """Fetch recent GitHub release notes for an npm package, resolved via
    its registry-declared repository URL."""
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
            "gh",
            "api",
            f"repos/{owner}/{repo}/releases",
            "--paginate",
            "-q",
            ".[:20]",
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
