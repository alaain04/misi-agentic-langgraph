"""Shared GitHub release-notes fetch for an npm package -- used by the
per-target remediation subagent's read_release_notes tool
(deepagent/tools.py) and by the tier classifier (classify.py)."""

from __future__ import annotations

import json
import re
import shlex

from src.domain.ports.container_run_port import ContainerRunPort
from src.utils.config import settings

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


async def resolve_package_info(
    package_name: str, repo_path: str, container: ContainerRunPort, docker_image: str
) -> tuple[str | None, tuple[str, str] | None]:
    command = (
        f"cd /workspace && npm view {shlex.quote(package_name)} "
        "version repository.url --json"
    )
    try:
        rc, stdout, _stderr = await container.run(
            image=docker_image,
            command=command,
            volume=f"{repo_path}:/workspace",
            run_as_root=True,
        )
        if rc != 0:
            return None, None

        data = json.loads(stdout.strip() or "{}")
    except Exception:
        return None, None

    version = (data.get("version") or "").strip() or None
    match = _GITHUB_REPO_RE.search((data.get("repository.url") or "").strip())
    repo = (match.group(1), match.group(2)) if match else None

    return version, repo


async def fetch_release_notes(
    package_name: str,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
    resolved_repo: tuple[str, str] | None = None,
) -> dict:
    """Fetch recent GitHub release notes for an npm package, resolved via
    its registry-declared repository URL.

    Pass resolved_repo when the caller already resolved it (e.g. via
    resolve_package_info) to skip a redundant npm view container spawn for
    the same package."""
    resolved = resolved_repo or await _resolve_github_repo(
        package_name, repo_path, container, docker_image
    )
    if resolved is None:
        return {
            "package_name": package_name,
            "available": False,
            "error": "could not resolve a GitHub repository for this package",
        }
    owner, repo = resolved
    command = (
        f"gh api {shlex.quote(f'repos/{owner}/{repo}/releases')} --paginate -q '.[:20]'"
    )
    secret_env = {"GH_TOKEN": settings.github_token} if settings.github_token else None
    rc, stdout, stderr = await container.run(
        image=settings.gh_docker_image,
        command=command,
        run_as_root=True,
        secret_env=secret_env,
    )
    if rc != 0:
        return {
            "package_name": package_name,
            "available": False,
            "error": stderr[:300],
        }
    try:
        releases = json.loads(stdout.strip() or "[]")
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
    full = await fetch_release_notes(
        package_name, repo_path, container, docker_image
    )
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


async def fetch_release_notes_page(
    package_name: str,
    page: int,
    from_version: str | None,
    to_version: str | None,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
    resolved_repo: tuple[str, str] | None = None,
) -> dict:
    """Fetch ONE page (per_page=100) of a package's GitHub releases
    directly -- no --paginate, so this never fetches more than the caller
    asks for (unlike fetch_release_notes, which always fetches a package's
    entire history before slicing to 20 -- see the design spec). Windows to
    (from_version, to_version] the same way fetch_release_notes_between
    does. has_more is True only when there's reason to believe an older,
    still-relevant release exists: this page was full AND its oldest tag is
    still above the window floor.
    """
    resolved = resolved_repo or await _resolve_github_repo(
        package_name, repo_path, container, docker_image
    )
    if resolved is None:
        return {
            "package_name": package_name,
            "available": False,
            "error": "could not resolve a GitHub repository for this package",
        }
    owner, repo = resolved
    per_page = 100
    command = (
        "gh api "
        f"{shlex.quote(f'repos/{owner}/{repo}/releases?per_page={per_page}&page={page}')}"
    )
    secret_env = {"GH_TOKEN": settings.github_token} if settings.github_token else None
    rc, stdout, stderr = await container.run(
        image=settings.gh_docker_image,
        command=command,
        run_as_root=True,
        secret_env=secret_env,
    )
    if rc != 0:
        return {
            "package_name": package_name,
            "available": False,
            "error": stderr[:300],
        }
    try:
        releases = json.loads(stdout.strip() or "[]")
    except json.JSONDecodeError:
        return {
            "package_name": package_name,
            "available": False,
            "error": "unparseable gh output",
        }

    low = _tag_version(from_version)
    high = _tag_version(to_version)
    windowed = [
        {
            "tag": r.get("tag_name"),
            "name": r.get("name"),
            "body": (r.get("body") or "")[:2000],
        }
        for r in releases
        if low is None or high is None or _tag_in_window(r.get("tag_name"), low, high)
    ]

    oldest = _tag_version(releases[-1].get("tag_name")) if releases else None
    has_more = (
        len(releases) == per_page
        and low is not None
        and (oldest is None or oldest > low)
    )

    return {
        "package_name": package_name,
        "available": True,
        "repository": f"{owner}/{repo}",
        "page": page,
        "has_more": has_more,
        "releases": windowed,
    }
