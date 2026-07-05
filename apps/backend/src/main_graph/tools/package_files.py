"""Local file and JSON analysis tools."""
from __future__ import annotations

import glob
import json
import logging
import os

from src.main_graph.tools.registry import register

logger = logging.getLogger(__name__)

_WIDE_RANGE_PATTERNS = ("*", "latest", "next", "x", "")


def _load_pkg(repo_path: str) -> dict:
    try:
        with open(os.path.join(repo_path, "package.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def _all_deps(pkg: dict) -> dict[str, str]:
    return {
        **pkg.get("dependencies", {}),
        **pkg.get("devDependencies", {}),
        **pkg.get("optionalDependencies", {}),
        **pkg.get("peerDependencies", {}),
    }


def _is_wide_range(spec: str) -> bool:
    s = spec.strip()
    return s in _WIDE_RANGE_PATTERNS or s.startswith(">=") or (s.startswith("^") and s[1:2] == "0")


@register("package_json", "Parses package.json; returns declared dependencies, scripts, engines, workspaces")
async def package_json(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    if not pkg:
        return {"error": "package.json not found or invalid"}
    return pkg


@register("package_lock", "Parses package-lock.json or lockfile; returns resolved versions and integrity hashes")
async def package_lock(repo_path: str) -> dict:
    for name in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"):
        path = os.path.join(repo_path, name)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    content = f.read()
                if name == "package-lock.json":
                    return {"lockfile": name, "data": json.loads(content)}
                return {"lockfile": name, "raw_size_bytes": len(content), "note": "non-JSON lockfile, use npm_list for resolved versions"}
            except Exception as exc:
                return {"error": str(exc)}
    return {"error": "no lockfile found"}


@register("version_ranges", "Detects broad version ranges (*, latest, wide ^ or >=) in package.json")
async def version_ranges(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = _all_deps(pkg)
    risky = [{"package": name, "range": spec} for name, spec in deps.items() if _is_wide_range(spec)]
    return {"risky_ranges": risky, "total_checked": len(deps)}


@register("dependency_confusion", "Detects internal/private package names that may be vulnerable to dependency confusion")
async def dependency_confusion(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = _all_deps(pkg)
    suspicious = []
    for name in deps:
        if any(kw in name.lower() for kw in ("internal", "private", "local", "corp", "intranet")):
            suspicious.append({"package": name, "reason": "name suggests private/internal package"})
    return {"suspicious_packages": suspicious, "note": "Verify these exist on npm registry"}


@register("install_scripts", "Detects packages with lifecycle scripts (preinstall, install, postinstall)")
async def install_scripts(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    scripts = pkg.get("scripts", {})
    lifecycle = ["preinstall", "install", "postinstall", "prepare", "prepack", "postpack"]
    found = [s for s in lifecycle if s in scripts]
    packages_with_scripts = []
    if found:
        packages_with_scripts.append({"package": pkg.get("name", "root"), "scripts": found})
    nm_path = os.path.join(repo_path, "node_modules")
    if os.path.isdir(nm_path):
        for entry in os.listdir(nm_path)[:100]:
            pkg_path = os.path.join(nm_path, entry, "package.json")
            try:
                with open(pkg_path) as f:
                    dep_pkg = json.load(f)
                dep_scripts = dep_pkg.get("scripts", {})
                dep_found = [s for s in lifecycle if s in dep_scripts]
                if dep_found:
                    packages_with_scripts.append({"package": entry, "scripts": dep_found})
            except Exception:
                pass
    return {"packages_with_scripts": packages_with_scripts}


@register("check_licenses", "Collects licenses for all dependencies and flags non-permissive licenses")
async def check_licenses(repo_path: str) -> dict:
    nm_path = os.path.join(repo_path, "node_modules")
    permissive = {"mit", "isc", "bsd-2-clause", "bsd-3-clause", "apache-2.0", "cc0-1.0", "0bsd", "unlicense"}
    results = []
    if os.path.isdir(nm_path):
        for entry in os.listdir(nm_path)[:200]:
            pkg_path = os.path.join(nm_path, entry, "package.json")
            try:
                with open(pkg_path) as f:
                    dep_pkg = json.load(f)
                lic = dep_pkg.get("license", "UNKNOWN")
                flagged = str(lic).lower() not in permissive
                results.append({"package": entry, "license": lic, "flagged": flagged})
            except Exception:
                pass
    flagged = [r for r in results if r["flagged"]]
    return {"licenses": results, "flagged_count": len(flagged), "flagged": flagged}


@register("duplicate_packages", "Finds multiple installed versions of the same package")
async def duplicate_packages(repo_path: str) -> dict:
    nm_path = os.path.join(repo_path, "node_modules")
    seen: dict[str, list[str]] = {}
    if os.path.isdir(nm_path):
        for entry in os.listdir(nm_path):
            pkg_path = os.path.join(nm_path, entry, "package.json")
            try:
                with open(pkg_path) as f:
                    dep_pkg = json.load(f)
                name = dep_pkg.get("name", entry)
                version = dep_pkg.get("version", "?")
                seen.setdefault(name, []).append(version)
            except Exception:
                pass
    duplicates = {name: versions for name, versions in seen.items() if len(versions) > 1}
    return {"duplicates": duplicates, "duplicate_count": len(duplicates)}


@register("missing_dependencies", "Finds packages imported in source files but absent from package.json")
async def missing_dependencies(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    declared = set(_all_deps(pkg).keys())
    imported: set[str] = set()
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "dist", "build")]
        for fname in files:
            if fname.endswith((".js", ".ts", ".jsx", ".tsx")):
                try:
                    content = open(os.path.join(root, fname)).read()
                    for line in content.splitlines():
                        line = line.strip()
                        for prefix in ("require('", 'require("', "from '", 'from "'):
                            if prefix in line:
                                idx = line.index(prefix) + len(prefix)
                                end = line.find(line[idx - 1], idx)
                                if end > idx:
                                    spec = line[idx:end].split("/")[0]
                                    if spec and not spec.startswith(".") and not spec.startswith("node:"):
                                        imported.add(spec)
                except Exception:
                    pass
    missing = [m for m in imported if m not in declared and not m.startswith("@types/")]
    return {"missing": missing, "checked_declared": len(declared)}


@register("dependency_size", "Estimates install size and identifies large dependencies")
async def dependency_size(repo_path: str) -> dict:
    nm_path = os.path.join(repo_path, "node_modules")
    if not os.path.isdir(nm_path):
        return {"error": "node_modules not found — run install first"}
    sizes: list[dict] = []
    for entry in os.listdir(nm_path):
        entry_path = os.path.join(nm_path, entry)
        if not os.path.isdir(entry_path):
            continue
        total = sum(
            os.path.getsize(os.path.join(root, f))
            for root, _, files in os.walk(entry_path)
            for f in files
        )
        sizes.append({"package": entry, "size_bytes": total})
    sizes.sort(key=lambda x: x["size_bytes"], reverse=True)
    total_bytes = sum(s["size_bytes"] for s in sizes)
    return {"total_bytes": total_bytes, "top_10_by_size": sizes[:10], "package_count": len(sizes)}


@register("dependency_stats", "Reports total, direct, transitive, dev, optional, and peer dependency counts")
async def dependency_stats(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    return {
        "direct": len(pkg.get("dependencies", {})),
        "dev": len(pkg.get("devDependencies", {})),
        "optional": len(pkg.get("optionalDependencies", {})),
        "peer": len(pkg.get("peerDependencies", {})),
        "total_declared": len(_all_deps(pkg)),
    }


@register("workspace_dependencies", "Lists dependencies per workspace for monorepo projects")
async def workspace_dependencies(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    workspaces = pkg.get("workspaces", [])
    if not workspaces:
        return {"workspaces": [], "note": "Not a monorepo or workspaces not declared"}
    results = []
    for pattern in (workspaces if isinstance(workspaces, list) else workspaces.get("packages", [])):
        for ws_path in glob.glob(os.path.join(repo_path, pattern)):
            ws_pkg_path = os.path.join(ws_path, "package.json")
            try:
                with open(ws_pkg_path) as f:
                    ws_pkg = json.load(f)
                results.append({
                    "workspace": os.path.relpath(ws_path, repo_path),
                    "name": ws_pkg.get("name"),
                    "dependencies": list(ws_pkg.get("dependencies", {}).keys()),
                })
            except Exception:
                pass
    return {"workspaces": results}


@register("read_file", "Reads a specific file from the cloned repo")
async def read_file(repo_path: str, relative_path: str) -> dict:
    full_path = os.path.normpath(os.path.join(repo_path, relative_path))
    repo_norm = os.path.normpath(repo_path)
    if full_path != repo_norm and not full_path.startswith(repo_norm + os.sep):
        return {"error": "path traversal not allowed"}
    try:
        with open(full_path) as f:
            content = f.read(50_000)
        return {"content": content, "truncated": os.path.getsize(full_path) > 50_000}
    except FileNotFoundError:
        return {"error": f"{relative_path} not found"}
    except Exception as exc:
        return {"error": str(exc)}


@register("list_directory", "Lists files at a path in the cloned repo")
async def list_directory(repo_path: str, relative_path: str = ".") -> dict:
    full_path = os.path.normpath(os.path.join(repo_path, relative_path))
    repo_norm = os.path.normpath(repo_path)
    if full_path != repo_norm and not full_path.startswith(repo_norm + os.sep):
        return {"error": "path traversal not allowed"}
    try:
        entries = os.listdir(full_path)
        return {"entries": sorted(entries), "count": len(entries)}
    except FileNotFoundError:
        return {"error": f"{relative_path} not found"}
    except Exception as exc:
        return {"error": str(exc)}
