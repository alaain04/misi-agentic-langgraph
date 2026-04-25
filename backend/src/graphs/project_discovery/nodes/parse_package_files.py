"""Node: parse_package_files — parse the provided package.json and lock file."""

import json
import re
from typing import Any

from src.graphs.project_discovery.state import DiscoveryState

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

    resolved: dict[str, str] = {}  # name -> resolved version
    edges: dict[str, list[str]] = {}  # name -> [dep names]

    # v1: "dependencies" is a flat dict keyed by package name
    # v2/v3: "packages" is keyed by "node_modules/<name>"; "" is the root
    if lockfile_version >= 2 and "packages" in data:
        for k, v in data["packages"].items():
            if not k:
                continue
            name = k.removeprefix("node_modules/")
            # Skip nested hoisted copies: "foo/node_modules/bar"
            if "node_modules/" not in name and not v.get("link", False):
                resolved[name] = v.get("version", "")
                dep_names = list(v.get("dependencies", {}).keys())
                if dep_names:
                    edges[name] = dep_names
    else:
        for name, v in data.get("dependencies", {}).items():
            resolved[name] = v.get("version", "")
            dep_names = list(v.get("dependencies", {}).keys())
            if dep_names:
                edges[name] = dep_names

    return {
        "format": "package-lock.json",
        "lockfile_version": lockfile_version,
        "resolved_packages": resolved,
        "resolved_packages_count": len(resolved),
        "package_edges": edges,
    }


def _parse_yarn_lock(content: str) -> dict[str, Any]:
    """
    Parse yarn.lock (v1 classic format) with a line-by-line approach.

    Each entry starts with one or more quoted specifiers followed by a colon,
    the resolved version appears on the next indented "version" line, and
    optional "dependencies:" block lists the package's own direct deps.
    """
    resolved: dict[str, str] = {}  # name -> resolved version
    edges: dict[str, list[str]] = {}  # name -> [dep names]
    header_re = re.compile(r'^"?(@?[^@"]+)@[^"]*"?[,:]')
    version_re = re.compile(r'^\s+version\s+"([^"]+)"')
    dep_entry_re = re.compile(r'^    "?(@?[^@"\s]+)')

    current_name: str | None = None
    in_deps = False

    for line in content.splitlines():
        if not line.startswith(" ") and not line.startswith("#"):
            in_deps = False
            m = header_re.match(line.strip())
            current_name = (m.group(1).strip('"') or None) if m else None
        elif current_name:
            vm = version_re.match(line)
            if vm:
                resolved[current_name] = vm.group(1)
            elif line.rstrip() == "  dependencies:":
                in_deps = True
            elif in_deps and line.startswith("    "):
                dm = dep_entry_re.match(line)
                if dm:
                    edges.setdefault(current_name, []).append(dm.group(1))
            elif line.startswith("  ") and not line.startswith("    "):
                in_deps = False

    return {
        "format": "yarn.lock",
        "resolved_packages": resolved,
        "resolved_packages_count": len(resolved),
        "package_edges": edges,
    }


def _split_pnpm_pkg(pkg: str) -> tuple[str, str]:
    """
    Split a pnpm lock entry key into (name, version).

    v5/v6 format: "/lodash/4.17.21" or "/@types/node/18.0.0"  (already lstripped of "/")
    v9+   format: "lodash@4.17.21"  or "@types/node@18.0.0"
    """
    if "@" in pkg[1:]:
        # v9+: name@version (scoped packages have "@" at index 0 AND later)
        name, _, version = pkg.rpartition("@")
    else:
        # v5/v6: name/version
        name, _, version = pkg.rpartition("/")
    return name, version


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
    resolved_packages: dict[str, str] = {}  # name -> resolved version
    edges: dict[str, list[str]] = {}  # name -> [dep names]

    section: str | None = None
    current_pkg: str | None = None  # package being parsed inside packages/snapshots
    in_pkg_deps = False  # inside a package's own "dependencies:" block

    for raw in content.splitlines():
        line = raw.rstrip()

        # lockfileVersion
        if line.startswith("lockfileVersion:"):
            lockfile_version = line.split(":", 1)[1].strip().strip("'\"")
            continue

        # Section headers
        if line == "dependencies:":
            section = "deps"
            current_pkg = None
            continue
        if line == "devDependencies:":
            section = "dev"
            current_pkg = None
            continue
        if line == "packages:":
            section = "packages"
            current_pkg = None
            continue
        if line == "snapshots:":
            section = "snapshots"
            current_pkg = None
            continue
        if line and not line.startswith(" "):
            # Any other top-level key resets the section
            section = None
            current_pkg = None
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

        # Package entry header: 2-space indent, ends with ":"
        # v5/v6: "  /lodash/4.17.21:" — v9+: "  lodash@4.17.21:"
        if (
            section in ("packages", "snapshots")
            and line.startswith("  ")
            and not line.startswith("   ")
        ):
            entry = line.strip()
            if entry.endswith(":") and not entry.startswith("#"):
                pkg = entry.rstrip(":").lstrip("/")
                if pkg:
                    name, version = _split_pnpm_pkg(pkg)
                    if name:
                        resolved_packages[name] = version
                        current_pkg = name
                        in_pkg_deps = False

        # Package sub-fields: 4-space indent
        elif section in ("packages", "snapshots") and current_pkg:
            if line.startswith("    ") and not line.startswith("     "):
                in_pkg_deps = line.rstrip() == "    dependencies:"
            # Dep entries inside a package: 6-space indent
            elif in_pkg_deps and line.startswith("      "):
                stripped = line.strip()
                if ":" in stripped and not stripped.startswith("#"):
                    dep_name = stripped.split(":")[0].strip()
                    edges.setdefault(current_pkg, []).append(dep_name)

    return {
        "format": "pnpm-lock.yaml",
        "lockfile_version": lockfile_version,
        "dependencies": dependencies,
        "dev_dependencies": dev_dependencies,
        "resolved_packages": resolved_packages,
        "resolved_packages_count": len(resolved_packages),
        "package_edges": edges,
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
    Parse the provided package.json and lock file contents into structured dicts.

    Populates: parsed_manifests, manifest_files
    """
    parsed_manifests: dict[str, Any] = {}
    manifest_files: list[str] = []

    for filename, content in [
        ("package.json", state.get("package_json_content", "")),
        (state.get("lock_file_name", ""), state.get("lock_file_content", "")),
    ]:
        if not filename or not content:
            continue
        parser = _PARSERS.get(filename)
        if not parser:
            parsed_manifests[filename] = {
                "format": filename,
                "parse_error": f"Unsupported file: {filename}",
            }
            continue
        try:
            parsed_manifests[filename] = parser(content)
            manifest_files.append(filename)
        except Exception as exc:  # noqa: BLE001
            parsed_manifests[filename] = {"format": filename, "parse_error": str(exc)}

    return {"parsed_manifests": parsed_manifests, "manifest_files": manifest_files}
