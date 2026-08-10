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
_SEMVER_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def _tag_version(tag: str | None) -> tuple[int, int, int] | None:
    if not tag:
        return None
    match = _SEMVER_TAG_RE.match(tag.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _tag_in_window(
    tag: str | None,
    low: tuple[int, int, int],
    high: tuple[int, int, int],
) -> bool:
    """Half-open (low, high]: exclude the installed version, include target."""
    v = _tag_version(tag)
    if v is None:
        return False
    return low < v <= high


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


async def resolve_latest_version(
    package_name: str, repo_path: str, container: ContainerRunPort, docker_image: str
) -> str | None:
    """The registry's current `latest` dist-tag for a package, or None when
    it cannot be resolved (offline, unpublished, non-zero npm exit).

    This is the only fact that proves a same-package upgrade EXISTS. Without
    it the pipeline can only infer "nothing newer" from the absence of GitHub
    release notes -- an LLM judgement that read a package with no further
    releases as "a clean upgrade with no breaking changes" and bumped it to
    the version already installed (job 6a7773a7576d0efd7796aa8c, `matcha`).
    """
    command = f"cd /workspace && npm view {shlex.quote(package_name)} version"
    try:
        rc, stdout, _stderr = await container.run(
            image=docker_image,
            command=command,
            volume=f"{repo_path}:/workspace",
            run_as_root=True,
        )
    except Exception:
        return None
    if rc != 0:
        return None
    version = stdout.strip().splitlines()[-1].strip() if stdout.strip() else ""
    return version or None


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


async def fetch_release_notes_between(
    package_name: str,
    from_version: str | None,
    to_version: str | None,
    repo_path: str,
    container,
    docker_image: str,
) -> dict:
    """Like fetch_release_notes, but keep only releases whose tag falls in the
    half-open window (from_version, to_version]. When either bound is missing
    or unparseable, return the unfiltered recent set (honest degradation)."""
    full = await fetch_release_notes(package_name, repo_path, container, docker_image)
    if not full.get("available"):
        return full
    low = _tag_version(from_version)
    high = _tag_version(to_version)
    if low is None or high is None:
        return full
    windowed = [
        r for r in full.get("releases", []) if _tag_in_window(r.get("tag"), low, high)
    ]
    return {**full, "releases": windowed}
