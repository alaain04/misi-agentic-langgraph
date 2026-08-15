"""External API tools (★) — all have 10s timeout and session-level cache."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.main_graph.tools.package_files import _all_deps, _load_pkg
from src.main_graph.tools.registry import register
from src.utils.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_cache: dict[str, Any] = {}


def clear_cache() -> None:
    _cache.clear()


async def _get(
    url: str, headers: dict | None = None, params: dict | None = None
) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, headers=headers or {}, params=params or {})
        r.raise_for_status()
        return r.json()


async def _npm_metadata(package_name: str) -> dict:
    key = f"npm:{package_name}"
    if key in _cache:
        return _cache[key]
    try:
        data = await asyncio.wait_for(
            _get(f"https://registry.npmjs.org/{package_name}"), _TIMEOUT
        )
        _cache[key] = data
        return data
    except Exception as exc:
        return {"error": str(exc)}


async def _npm_weekly_downloads(package_name: str) -> int | None:
    key = f"npm_dl:{package_name}"
    if key in _cache:
        return _cache[key]
    try:
        encoded = package_name.replace("/", "%2F")
        data = await asyncio.wait_for(
            _get(f"https://api.npmjs.org/downloads/point/last-week/{encoded}"),
            _TIMEOUT,
        )
        count = data.get("downloads")
        _cache[key] = count
        return count
    except Exception:
        return None


@register(
    "github_advisory",
    "Queries GitHub Advisory Database (GraphQL) for known vulnerabilities in a package",
)
async def github_advisory(package_name: str, ecosystem: str = "NPM") -> dict:
    key = f"advisory:{ecosystem}:{package_name}"
    if key in _cache:
        return _cache[key]
    token = settings.github_token
    if not token:
        return {"error": "GITHUB_TOKEN not set", "advisories": []}
    query = """
    query($ecosystem: SecurityAdvisoryEcosystem!, $package: String!) {
      securityVulnerabilities(ecosystem: $ecosystem, package: $package, first: 20) {
        nodes {
          severity
          updatedAt
          advisory { summary ghsaId permalink publishedAt }
          vulnerableVersionRange
          firstPatchedVersion { identifier }
        }
      }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                "https://api.github.com/graphql",
                json={
                    "query": query,
                    "variables": {"ecosystem": ecosystem, "package": package_name},
                },
                headers={
                    "Authorization": f"bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            r.raise_for_status()
            data = r.json()
        nodes = data.get("data", {}).get("securityVulnerabilities", {}).get("nodes", [])
        result = {"package": package_name, "advisories": nodes, "count": len(nodes)}
        _cache[key] = result
        return result
    except Exception as exc:
        return {"error": str(exc), "advisories": []}


@register(
    "osv_lookup", "Queries OSV.dev for vulnerability records for a package version"
)
async def osv_lookup(
    package_name: str, version: str = "", ecosystem: str = "npm"
) -> dict:
    key = f"osv:{ecosystem}:{package_name}:{version}"
    if key in _cache:
        return _cache[key]
    payload: dict[str, Any] = {
        "package": {"name": package_name, "ecosystem": ecosystem}
    }
    if version:
        payload["version"] = version
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post("https://api.osv.dev/v1/query", json=payload)
            r.raise_for_status()
            data = r.json()
        vulns = data.get("vulns", [])
        result = {
            "package": package_name,
            "version": version,
            "vulnerabilities": vulns,
            "count": len(vulns),
        }
        _cache[key] = result
        return result
    except Exception as exc:
        return {"error": str(exc), "vulnerabilities": []}


@register(
    "package_health_data",
    "Reports raw npm registry health facts (release recency, weekly downloads, "
    "maintainer count) for every direct dependency. Returns data only — no risk "
    "judgment or flagging; the caller decides what counts as a maintenance risk.",
)
async def package_health_data(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = list(_all_deps(pkg).keys())
    deps_to_check = deps[:30]  # limit to avoid rate limiting
    metas, downloads = await asyncio.gather(
        asyncio.gather(*[_npm_metadata(d) for d in deps_to_check]),
        asyncio.gather(*[_npm_weekly_downloads(d) for d in deps_to_check]),
    )
    packages = []
    for dep, meta, weekly_downloads in zip(deps_to_check, metas, downloads):
        if "error" in meta:
            packages.append({"package": dep, "error": meta["error"]})
            continue
        time_data = meta.get("time", {})
        packages.append(
            {
                "package": dep,
                "created": time_data.get("created", ""),
                "last_modified": time_data.get("modified", ""),
                "weekly_downloads": weekly_downloads,
                "maintainer_count": len(meta.get("maintainers", [])),
                "latest_version": meta.get("dist-tags", {}).get("latest", ""),
            }
        )
    return {
        "packages": packages,
        "checked": len(deps_to_check),
        "total_deps": len(deps),
    }


_POPULAR_PACKAGES = {
    "lodash",
    "express",
    "react",
    "vue",
    "angular",
    "webpack",
    "babel",
    "eslint",
    "prettier",
    "jest",
    "mocha",
    "axios",
    "moment",
    "dayjs",
    "uuid",
    "chalk",
    "commander",
    "yargs",
    "dotenv",
    "cors",
    "helmet",
    "passport",
    "sequelize",
    "mongoose",
    "redis",
    "bull",
    "socket.io",
    "ws",
    "http-proxy",
    "node-fetch",
}


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        return _edit_distance(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


@register(
    "typosquat_detection",
    "Detects package names similar to popular packages (edit distance <= 2)",
)
async def typosquat_detection(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = list(_all_deps(pkg).keys())
    flagged = []
    for dep in deps:
        dep_clean = dep.lstrip("@").split("/")[-1]
        for popular in _POPULAR_PACKAGES:
            dist = _edit_distance(dep_clean, popular)
            if dep_clean != popular and dist <= 2:
                flagged.append(
                    {"package": dep, "similar_to": popular, "edit_distance": dist}
                )
                break
    return {"potential_typosquats": flagged, "checked": len(deps)}


def _package_name_variants(package_name: str) -> set[str]:
    variants = {package_name.lower()}
    variants.add(package_name.lstrip("@").split("/")[-1].lower())
    return variants


def _mentions_package(item: dict, package_name: str) -> bool:
    text = (item.get("title", "") + " " + item.get("content", "")).lower()
    return any(v in text for v in _package_name_variants(package_name))


@register(
    "web_search",
    "Searches the web for evidence (advisories, issues, releases, migration guides) "
    "about a SPECIFIC package's SPECIFIC flagged reason. Always scoped to "
    "package_name — query must describe the concrete reason it was flagged (a CVE "
    "id, 'prototype pollution', 'license conflict', etc.), never the bare package "
    "name alone. Results that don't actually mention package_name are dropped "
    "server-side so this never surfaces evidence about an unrelated package.",
)
async def web_search(package_name: str, query: str) -> dict:
    if not settings.tavily_api_key:
        return {"error": "TAVILY_API_KEY not configured", "results": []}
    full_query = (
        query if package_name.lower() in query.lower() else f"{package_name} {query}"
    )
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings.tavily_api_key,
                    "query": full_query,
                    "max_results": 5,
                },
            )
            r.raise_for_status()
            data = r.json()
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
            }
            for item in data.get("results", [])
            if _mentions_package(item, package_name)
        ]
        return {"query": full_query, "package_name": package_name, "results": results}
    except Exception as exc:
        return {"error": str(exc), "results": []}
