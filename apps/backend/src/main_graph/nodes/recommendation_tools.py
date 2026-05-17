"""HTTP lookup functions for the recommendation node — npm registry and GitHub."""

from __future__ import annotations

import os

import httpx

_NPM_REGISTRY = "https://registry.npmjs.org"
_NPM_SEARCH = "https://registry.npmjs.org/-/v1/search"
_NPM_DOWNLOADS = "https://api.npmjs.org/downloads/point/last-week"
_GITHUB_API = "https://api.github.com"


async def _search_npm(query: str, max_results: int = 10) -> list[dict]:
    """Search npm registry for packages matching query."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _NPM_SEARCH,
            params={"text": query, "size": max_results},
            timeout=10.0,
        )
        resp.raise_for_status()
        return [
            {
                "name": obj["package"]["name"],
                "description": obj["package"].get("description", ""),
                "weekly_downloads": None,
                "last_publish": obj["package"].get("date"),
            }
            for obj in resp.json().get("objects", [])
        ]


async def _get_npm_metadata(package_name: str) -> dict:
    """Fetch full npm registry metadata for package_name."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_NPM_REGISTRY}/{package_name}",
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        dist_tags = data.get("dist-tags", {})

        weekly_downloads: int | None = None
        try:
            dl_resp = await client.get(
                f"{_NPM_DOWNLOADS}/{package_name}",
                timeout=10.0,
            )
            if dl_resp.status_code == 200:
                weekly_downloads = dl_resp.json().get("downloads")
        except Exception:
            pass

        _repo = data.get("repository")
        return {
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "latest_version": dist_tags.get("latest"),
            "license": data.get("license"),
            "deprecated": data.get("deprecated"),
            "homepage": data.get("homepage"),
            "repository": _repo.get("url") if isinstance(_repo, dict) else _repo,
            "maintainers": [m.get("name") for m in data.get("maintainers", [])],
            "weekly_downloads": weekly_downloads,
        }


async def _get_github_summary(owner: str, repo: str) -> dict:
    """Fetch key health signals from GitHub for owner/repo."""
    headers: dict[str, str] = {}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{owner}/{repo}",
                headers=headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "stars": data.get("stargazers_count"),
                "open_issues": data.get("open_issues_count"),
                "last_commit": data.get("pushed_at"),
                "archived": data.get("archived", False),
            }
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError):
            return {}


async def _compare_packages(original: str, alternatives: list[str]) -> dict:
    """Fetch npm metadata for original and all alternatives for side-by-side comparison."""
    results: dict[str, dict] = {}
    for name in [original] + alternatives:
        try:
            results[name] = await _get_npm_metadata(name)
        except Exception:
            results[name] = {"name": name}
    return results
