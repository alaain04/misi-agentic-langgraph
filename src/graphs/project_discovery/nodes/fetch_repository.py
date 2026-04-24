"""Node: fetch_repository — resolve the GitHub repo and retrieve basic metadata."""

import re

import httpx

from src.graphs.project_discovery.state import DiscoveryState

_GITHUB_API = "https://api.github.com"

# Matches https://github.com/{owner}/{repo}[.git][/...]
_GITHUB_RE = re.compile(
    r"github\.com[/:](?P<owner>[^/]+)/(?P<repo>[^/?.#]+?)(?:\.git)?(?:[/?#].*)?$"
)


def _parse_github_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL. Raises ValueError on failure."""
    m = _GITHUB_RE.search(url)
    if not m:
        raise ValueError(f"Cannot parse GitHub owner/repo from URL: {url!r}")
    return m.group("owner"), m.group("repo")


def _auth_headers(token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def fetch_repository(state: DiscoveryState) -> dict:
    """
    Parse the repository URL and call the GitHub REST API to retrieve
    the repo's name and default branch.

    Populates: repo_owner, repo_name, default_branch
    """
    repo_url: str = state["repo_url"]
    token: str | None = state.get("token")

    try:
        owner, repo = _parse_github_url(repo_url)
    except ValueError as exc:
        return {"discovery_error": str(exc)}

    api_url = f"{_GITHUB_API}/repos/{owner}/{repo}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(api_url, headers=_auth_headers(token))

    if resp.status_code == 404:
        return {"discovery_error": f"Repository not found or private: {owner}/{repo}"}
    if resp.status_code == 403:
        return {
            "discovery_error": (
                "GitHub API rate limit exceeded or access forbidden. "
                "Provide a token via the `token` field."
            )
        }
    resp.raise_for_status()

    data = resp.json()

    return {
        "repo_owner": owner,
        "repo_name": data.get("name", repo),
        "default_branch": data.get("default_branch", "main"),
    }
