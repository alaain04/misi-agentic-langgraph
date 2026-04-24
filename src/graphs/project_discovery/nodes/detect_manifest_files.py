"""Node: detect_manifest_files — find package-manager files via the GitHub tree API."""

import httpx

from src.graphs.project_discovery.state import DiscoveryState

_GITHUB_API = "https://api.github.com"

# Recognised manifest filenames (order reflects priority for PM detection).
_MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    }
)


def _auth_headers(token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def detect_manifest_files(state: DiscoveryState) -> dict:
    """
    Walk the repository tree (non-recursive root listing + one level deep)
    to locate JS package-manager manifest files.

    Uses the GitHub Trees API with recursive=1 to traverse the full tree.
    Files are filtered to the known manifest set; only the first occurrence
    of each manifest type is kept to avoid monorepo confusion.

    Populates: detected_files, manifest_files
    """
    if state.get("discovery_error"):
        return {}

    owner = state["repo_owner"]
    repo = state["repo_name"]
    branch = state["default_branch"]
    token: str | None = state.get("token")

    tree_url = f"{_GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(tree_url, headers=_auth_headers(token))

    if resp.status_code == 409:
        # Empty repository
        return {"detected_files": [], "manifest_files": []}
    resp.raise_for_status()

    data = resp.json()
    tree = data.get("tree", [])

    # Collect matching paths, deduplicated per manifest type (first wins).
    seen_names: set[str] = set()
    found: list[str] = []

    for entry in tree:
        if entry.get("type") != "blob":
            continue
        path: str = entry["path"]
        filename = path.split("/")[-1]
        if filename in _MANIFEST_NAMES and filename not in seen_names:
            seen_names.add(filename)
            found.append(path)

    return {
        "detected_files": found,
        # manifest_files is the public output field (same data at this stage)
        "manifest_files": found,
    }
