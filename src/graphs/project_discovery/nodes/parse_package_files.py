"""Node: parse_package_files — fetch and parse each detected manifest file."""

import base64
import json
import re
from typing import Any

import httpx

from src.graphs.project_discovery.state import DiscoveryState

_GITHUB_API = "https://api.github.com"


def _auth_headers(token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ── Per-format parsers ────────────────────────────────────────────────────────


def _parse_package_json(content: str) -> dict[str, Any]:
    data = json.loads(content)
    return {
        "format": "package.json",
        "name": data.get("name", ""),
        "version": data.get("version", ""),
        "dependencies": data.get("dependencies", {}),
        "dev_dependencies": data.get("devDependencies", {}),
        "peer_dependencies": data.get("peerDependencies", {}),
    }


def _parse_package_lock_json(content: str) -> dict[str, Any]:
    data = json.loads(content)
    lockfile_version = data.get("lockfileVersion", 1)

    # v1: "dependencies" is a flat dict of resolved packages
    # v2/v3: "packages" is the canonical source; key "" is the root
    if lockfile_version >= 2 and "packages" in data:
        packages = {
            k: v
            for k, v in data["packages"].items()
            if k and not k.startswith("node_modules/")
        }
    else:
        packages = data.get("dependencies", {})

    return {
        "format": "package-lock.json",
        "lockfile_version": lockfile_version,
        "resolved_packages_count": len(packages),
    }


def _parse_yarn_lock(content: str) -> dict[str, Any]:
    """
    Parse yarn.lock (v1 classic format) with a line-by-line approach.

    Each entry starts with one or more quoted specifiers followed by a colon.
    Example:
        "lodash@^4.17.21":
          version "4.17.21"
    """
    packages: list[str] = []
    header_re = re.compile(r'^"?(@?[^@"]+)@[^"]*"?[,:]')

    for line in content.splitlines():
        m = header_re.match(line.strip())
        if m and not line.startswith(" ") and not line.startswith("#"):
            name = m.group(1).strip('"')
            if name and name not in packages:
                packages.append(name)

    return {
        "format": "yarn.lock",
        "resolved_packages_count": len(packages),
        "package_names": packages,
    }


def _parse_pnpm_lock(content: str) -> dict[str, Any]:
    """
    Parse pnpm-lock.yaml with a minimal line-by-line strategy.

    Direct dependencies appear under the top-level `dependencies:` or
    `devDependencies:` sections before the first `packages:` block.
    Resolved transitive packages are counted from the `packages:` section
    (v5/v6: keys starting with `/`; v9+: keys like `pkg@version`).
    """
    dependencies: dict[str, str] = {}
    dev_dependencies: dict[str, str] = {}
    lockfile_version = ""
    resolved_packages: list[str] = []

    section: str | None = None

    for raw in content.splitlines():
        line = raw.rstrip()

        # lockfileVersion
        if line.startswith("lockfileVersion:"):
            lockfile_version = line.split(":", 1)[1].strip().strip("'\"")
            continue

        # Section headers
        if line == "dependencies:":
            section = "deps"
            continue
        if line == "devDependencies:":
            section = "dev"
            continue
        if line == "packages:":
            section = "packages"
            continue
        if line == "snapshots:":
            section = "snapshots"
            continue
        if line and not line.startswith(" "):
            # Any other top-level key resets the section
            section = None
            continue

        if section in ("deps", "dev") and line.startswith("  "):
            # e.g. "  express:" or "  express: 4.18.0" or "    specifier: ^4.x"
            stripped = line.strip()
            if stripped.startswith("specifier:"):
                continue  # version specifier sub-key — skip
            if ":" in stripped and not stripped.startswith("#"):
                name, _, spec = stripped.partition(":")
                spec = spec.strip().strip("'\"")
                if section == "deps":
                    dependencies[name.strip()] = spec
                else:
                    dev_dependencies[name.strip()] = spec

        # Count resolved packages: top-level entries inside packages/snapshots sections
        # v5/v6: "  /lodash/4.17.21:" or "  /lodash@4.17.21:"
        # v9:    "  lodash@4.17.21:"
        if (
            section in ("packages", "snapshots")
            and line.startswith("  ")
            and not line.startswith("   ")
        ):
            entry = line.strip()
            if entry.endswith(":") and not entry.startswith("#"):
                pkg = entry.rstrip(":")
                if pkg:
                    resolved_packages.append(pkg.lstrip("/"))

    return {
        "format": "pnpm-lock.yaml",
        "lockfile_version": lockfile_version,
        "dependencies": dependencies,
        "dev_dependencies": dev_dependencies,
        "resolved_packages_count": len(resolved_packages),
        "package_names": resolved_packages,
    }


_PARSERS = {
    "package.json": _parse_package_json,
    "package-lock.json": _parse_package_lock_json,
    "yarn.lock": _parse_yarn_lock,
    "pnpm-lock.yaml": _parse_pnpm_lock,
}


# ── Node ─────────────────────────────────────────────────────────────────────


async def parse_package_files(state: DiscoveryState) -> dict:
    """
    Fetch each detected manifest file from the GitHub Contents API and
    parse it into a structured dict.

    Populates: parsed_manifests
    """
    if state.get("discovery_error"):
        return {}

    detected: list[str] = state.get("detected_files", [])
    if not detected:
        return {"parsed_manifests": {}}

    owner = state["repo_owner"]
    repo = state["repo_name"]
    branch = state["default_branch"]
    token: str | None = state.get("token")

    parsed_manifests: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=20) as client:
        for path in detected:
            filename = path.split("/")[-1]
            parser = _PARSERS.get(filename)
            if not parser:
                continue

            url = f"{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
            resp = await client.get(
                url,
                headers=_auth_headers(token),
                params={"ref": branch},
            )

            if resp.status_code == 404:
                continue
            resp.raise_for_status()

            data = resp.json()
            # GitHub returns base64-encoded content
            raw_bytes = base64.b64decode(data["content"])
            content = raw_bytes.decode("utf-8", errors="replace")

            try:
                parsed_manifests[path] = parser(content)
            except Exception as exc:  # noqa: BLE001
                parsed_manifests[path] = {"format": filename, "parse_error": str(exc)}

    return {"parsed_manifests": parsed_manifests}
