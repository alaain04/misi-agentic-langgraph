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
    "package_reputation",
    "Reports package age, maintainers, release cadence, popularity, and weekly "
    "downloads via npm registry",
)
async def package_reputation(package_name: str) -> dict:
    meta, weekly_downloads = await asyncio.gather(
        _npm_metadata(package_name),
        _npm_weekly_downloads(package_name),
    )
    if "error" in meta:
        return meta
    time_data = meta.get("time", {})
    versions = list(time_data.keys())
    created = time_data.get("created", "")
    modified = time_data.get("modified", "")
    maintainers = meta.get("maintainers", [])
    latest_ver = meta.get("dist-tags", {}).get("latest", "")
    return {
        "package": package_name,
        "created": created,
        "last_modified": modified,
        "version_count": len([v for v in versions if v not in ("created", "modified")]),
        "latest_version": latest_ver,
        "maintainer_count": len(maintainers),
        "maintainers": [m.get("name") for m in maintainers],
        "weekly_downloads": weekly_downloads,
    }


@register(
    "unmaintained_packages",
    "Flags packages with no releases for 12+ months based on npm registry data",
)
async def unmaintained_packages(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = list(_all_deps(pkg).keys())
    cutoff = datetime.now(UTC) - timedelta(days=365)
    flagged = []
    deps_to_check = deps[:30]  # limit to avoid rate limiting
    metas = await asyncio.gather(*[_npm_metadata(d) for d in deps_to_check])
    for dep, meta in zip(deps_to_check, metas):
        if "error" in meta:
            continue
        modified_str = meta.get("time", {}).get("modified", "")
        try:
            modified = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
            if modified < cutoff:
                flagged.append({"package": dep, "last_modified": modified_str})
        except Exception:
            pass
    return {"unmaintained": flagged, "checked": min(len(deps), 30)}


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


_LOW_WEEKLY_DOWNLOADS = 1000


@register(
    "high_risk_packages",
    "Flags packages with unusual risk characteristics (very new or abandoned). "
    "Maintainer count alone is never used as a risk signal — a single maintainer "
    "is normal for the npm ecosystem and is not, by itself, evidence of risk.",
)
async def high_risk_packages(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = list(_all_deps(pkg).keys())
    cutoff_new = datetime.now(UTC) - timedelta(days=90)
    cutoff_abandoned = datetime.now(UTC) - timedelta(days=730)
    flagged = []
    deps_to_check = deps[:30]
    metas, downloads = await asyncio.gather(
        asyncio.gather(*[_npm_metadata(d) for d in deps_to_check]),
        asyncio.gather(*[_npm_weekly_downloads(d) for d in deps_to_check]),
    )
    for dep, meta, weekly_downloads in zip(deps_to_check, metas, downloads):
        if "error" in meta:
            continue
        # Real, healthy adoption overrides every other signal: a package with
        # steady weekly downloads is demonstrably in active use, even if it
        # hasn't shipped a release in a while (many mature libs go long
        # stretches without needing one) or has a single npm publisher.
        has_healthy_downloads = (
            weekly_downloads is not None and weekly_downloads >= _LOW_WEEKLY_DOWNLOADS
        )
        if has_healthy_downloads:
            continue
        time_data = meta.get("time", {})
        created_str = time_data.get("created", "")
        modified_str = time_data.get("modified", "")
        reasons = []
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created > cutoff_new:
                reasons.append("very new package (<90 days)")
        except Exception:
            pass
        is_abandoned = False
        try:
            modified = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
            is_abandoned = modified < cutoff_abandoned
            if is_abandoned:
                reasons.append("abandoned (>2 years no release)")
        except Exception:
            pass
        if reasons:
            flagged.append({"package": dep, "reasons": reasons})
    return {"high_risk": flagged, "checked": min(len(deps), 30)}


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
        query
        if package_name.lower() in query.lower()
        else f"{package_name} {query}"
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
