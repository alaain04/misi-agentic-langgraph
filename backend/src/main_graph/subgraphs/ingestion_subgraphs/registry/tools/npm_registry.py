"""npm registry API tools — never raise, always return JSON."""

from __future__ import annotations

import json

import httpx
from langchain_core.tools import tool

from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import npm_cache_dao


def _parse_person(p):
    if p is None:
        return None
    if isinstance(p, str):
        return {"name": p, "email": None, "url": None}
    return {"name": p.get("name"), "email": p.get("email"), "url": p.get("url")}


@tool
async def check_package_cache(package_name: str, max_age_days: int = 7) -> str:
    """
    Check if fresh npm metadata for a package is already in the local cache.

    Returns JSON: {found: true, data: {...}} or {found: false}.
    """
    try:
        cached = await npm_cache_dao.find_cached_package(package_name, max_age_days)
        if cached is None:
            return json.dumps({"found": False})
        doc = cached.model_dump()
        doc["fetched_at"] = doc["fetched_at"].isoformat()
        if doc.get("dist"):
            doc["dist"] = {k: v for k, v in doc["dist"].items() if v is not None}
        return json.dumps({"found": True, "data": doc})
    except Exception as exc:
        return json.dumps({"found": False, "error": str(exc)})


@tool
async def fetch_registry_metadata(package_name: str) -> str:
    """
    Fetch npm registry metadata for a package.

    Returns JSON with success=true and all available package fields,
    or {success: false, error} on failure.
    """
    try:
        url = f"https://registry.npmjs.org/{package_name}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        if resp.status_code == 404:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Package '{package_name}' not found on npm registry",
                }
            )
        resp.raise_for_status()
        data = resp.json()

        latest_version = data.get("dist-tags", {}).get("latest", "")
        dist_tags = data.get("dist-tags", {})
        version_data = data.get("versions", {}).get(latest_version, {})

        # Description / keywords / license / homepage
        description = data.get("description") or version_data.get("description")
        keywords = data.get("keywords") or version_data.get("keywords") or []
        license_ = version_data.get("license") or data.get("license")
        homepage = version_data.get("homepage") or data.get("homepage")

        # Repository
        repo = data.get("repository") or version_data.get("repository") or {}
        if isinstance(repo, str):
            raw_repo_url = repo
            repo_type = None
        else:
            raw_repo_url = repo.get("url", "")
            repo_type = repo.get("type")
        repo_url = (
            raw_repo_url.replace("git+", "")
            .replace("git://", "https://")
            .rstrip(".git")
        )
        if "github.com" not in repo_url:
            repo_url = ""
            repo_type = None

        # Bugs
        bugs = data.get("bugs") or version_data.get("bugs") or {}
        bugs_url = bugs.get("url") if isinstance(bugs, dict) else None
        bugs_email = bugs.get("email") if isinstance(bugs, dict) else None

        # Readme
        readme = data.get("readme")
        readme_filename = data.get("readmeFilename")

        # Timestamps
        time_created = data.get("time", {}).get("created")
        time_modified = data.get("time", {}).get("modified")

        # Deprecation
        deprecated_raw = version_data.get("deprecated")
        deprecated = bool(deprecated_raw)
        deprecated_message = deprecated_raw if isinstance(deprecated_raw, str) else None

        # Version-level fields
        main = version_data.get("main")
        scripts = version_data.get("scripts") or {}
        engines = version_data.get("engines") or {}

        # Author / contributors / maintainers
        author = _parse_person(version_data.get("author"))
        contributors = [
            _parse_person(c)
            for c in (version_data.get("contributors") or [])
            if _parse_person(c) is not None
        ]
        raw_maintainers = (
            data.get("maintainers") or version_data.get("maintainers") or []
        )
        maintainers = [
            _parse_person(m)
            for m in raw_maintainers
            if _parse_person(m) is not None
        ]

        # Dependencies
        dependencies = version_data.get("dependencies") or {}
        dev_dependencies = version_data.get("devDependencies") or {}
        peer_dependencies = version_data.get("peerDependencies") or {}
        optional_dependencies = version_data.get("optionalDependencies") or {}

        # Dist
        raw_dist = version_data.get("dist", {})
        dist = {
            "tarball": raw_dist.get("tarball"),
            "shasum": raw_dist.get("shasum"),
            "integrity": raw_dist.get("integrity"),
            "file_count": raw_dist.get("fileCount"),
            "unpacked_size": raw_dist.get("unpackedSize"),
        }

        # npm internals
        npm_user = (version_data.get("_npmUser") or {}).get("name")
        npm_version = version_data.get("_npmVersion")
        node_version = version_data.get("_nodeVersion")
        has_shrinkwrap = version_data.get("_hasShrinkwrap")

        return json.dumps(
            {
                "success": True,
                "name": package_name,
                "latest_version": latest_version,
                "dist_tags": dist_tags,
                "description": description,
                "keywords": keywords,
                "license": license_,
                "homepage": homepage,
                "repository_url": repo_url or None,
                "repository_type": repo_type,
                "bugs_url": bugs_url,
                "bugs_email": bugs_email,
                "readme": readme,
                "readme_filename": readme_filename,
                "time_created": time_created,
                "time_modified": time_modified,
                "deprecated": deprecated,
                "deprecated_message": deprecated_message,
                "main": main,
                "scripts": scripts,
                "engines": engines,
                "author": author,
                "contributors": contributors,
                "maintainers": maintainers,
                "dependencies": dependencies,
                "dev_dependencies": dev_dependencies,
                "peer_dependencies": peer_dependencies,
                "optional_dependencies": optional_dependencies,
                "dist": dist,
                "npm_user": npm_user,
                "npm_version": npm_version,
                "node_version": node_version,
                "has_shrinkwrap": has_shrinkwrap,
            }
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


@tool
async def fetch_npm_downloads(package_name: str) -> str:
    """
    Fetch npm download count for the previous calendar month.

    Returns JSON: {success, downloads} or {success, error}.
    """
    try:
        url = f"https://api.npmjs.org/downloads/point/last-month/{package_name}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        if resp.status_code == 404:
            return json.dumps(
                {"success": False, "error": f"No download data for '{package_name}'"}
            )
        resp.raise_for_status()
        data = resp.json()
        return json.dumps({"success": True, "downloads": data.get("downloads", 0)})
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})
