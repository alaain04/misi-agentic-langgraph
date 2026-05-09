"""Analyze node for the Registry subgraph.

Fetches npm registry metadata and download stats for every direct dependency,
maps results to PackageRecord domain objects, and stores the primary package's
GitHub repository coordinates for downstream subgraphs.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from langchain_core.messages import ToolMessage

from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import (
    npm_cache_dao,
    registry_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.registry.models import (
    NpmAuthor,
    NpmDist,
    NpmPackageCache,
    PackageRecord,
    RegistryEntry,
)
from src.main_graph.subgraphs.ingestion_subgraphs.registry.state import RegistryState
from src.main_graph.subgraphs.ingestion_subgraphs.registry.tools.npm_registry import (
    check_package_cache,
    fetch_npm_downloads,
    fetch_registry_metadata,
)
from src.utils.llm import Model, get_llm, parse_llm_json

_log = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_TOOLS = [check_package_cache, fetch_registry_metadata, fetch_npm_downloads]

_SYSTEM_PROMPT = """\
You are a tool-calling agent that fetches npm registry metadata and download statistics.
For each package, follow these steps in order:

1. Call check_package_cache with the package name to check the local cache.
   - If found=true: use the cached data directly. Do NOT call fetch_registry_metadata.
     Read monthly_downloads from the cached data — do NOT call fetch_npm_downloads.
   - If found=false: proceed to step 2.

2. Call fetch_registry_metadata with the package name to fetch full data from npm.

3. Call fetch_npm_downloads ONLY when found=false in step 1.
   If data came from cache, skip this step and use monthly_downloads from the cache.

After all tool calls complete, return a single JSON object with exactly these keys:
  name, latest_version, dist_tags, description, keywords, license, homepage,
  repository_url, repository_type, bugs_url, bugs_email, readme, readme_filename,
  time_created, time_modified, deprecated, deprecated_message, main, scripts,
  engines, author, contributors, maintainers, dependencies, dev_dependencies,
  peer_dependencies, optional_dependencies, dist, npm_user, npm_version,
  node_version, has_shrinkwrap, monthly_downloads, from_cache, error.

Set from_cache=true if data came from check_package_cache, false otherwise.
Set error to the error message if fetch_registry_metadata returned
success=false, else null.
Set monthly_downloads from the downloads tool result (if fetched), or from the
cached data's monthly_downloads field (if found=true). Use null only on error.\
"""


def _parse_repository_owner_name(
    repository_url: str | None,
) -> tuple[str | None, str | None]:
    if not repository_url or "github.com" not in repository_url:
        return None, None
    parts = repository_url.rstrip("/").split("/")
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return None, None


def _strip_version_range(version_spec: str) -> str:
    return version_spec.lstrip("^~>=< ")


def _parse_author(raw) -> NpmAuthor | None:
    if isinstance(raw, dict):
        return NpmAuthor(
            name=raw.get("name"), email=raw.get("email"), url=raw.get("url")
        )
    return None


def _parse_persons(lst) -> list[NpmAuthor]:
    return [
        NpmAuthor(name=p.get("name"), email=p.get("email"), url=p.get("url"))
        for p in (lst or [])
        if isinstance(p, dict)
    ]


def _build_package_record(
    pkg_name: str, current_version: str, info: dict
) -> PackageRecord:
    latest = info.get("latest_version") or ""
    outdated = bool(current_version and latest and current_version != latest)

    dist_raw = info.get("dist")
    dist = (
        NpmDist(
            tarball=dist_raw.get("tarball"),
            shasum=dist_raw.get("shasum"),
            integrity=dist_raw.get("integrity"),
            file_count=dist_raw.get("file_count"),
            unpacked_size=dist_raw.get("unpacked_size"),
        )
        if isinstance(dist_raw, dict)
        else None
    )

    return PackageRecord(
        name=pkg_name,
        current_version=current_version,
        latest_version=latest or None,
        outdated=outdated,
        deprecated=bool(info.get("deprecated")),
        deprecated_message=info.get("deprecated_message"),
        description=info.get("description"),
        license=info.get("license"),
        homepage=info.get("homepage"),
        repository_url=info.get("repository_url"),
        keywords=info.get("keywords") or [],
        monthly_downloads=info.get("monthly_downloads"),
        dependencies=info.get("dependencies") or {},
        dev_dependencies=info.get("dev_dependencies") or {},
        peer_dependencies=info.get("peer_dependencies") or {},
        optional_dependencies=info.get("optional_dependencies") or {},
        engines=info.get("engines") or {},
        dist=dist,
        author=_parse_author(info.get("author")),
        maintainers=_parse_persons(info.get("maintainers")),
        has_shrinkwrap=info.get("has_shrinkwrap"),
    )


async def _fetch_package_info(llm_with_tools: object, package_name: str) -> dict:
    """Run the LLM agent for one package. Returns a raw result dict."""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Fetch registry metadata and download stats"
                f" for npm package '{package_name}'."
            ),
        },
    ]
    tool_map = {t.name: t for t in _TOOLS}

    for _ in range(6):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            try:
                return parse_llm_json(response.content or "")
            except Exception:
                continue

        for tc in response.tool_calls:
            tool_fn = tool_map.get(tc["name"])
            if tool_fn:
                tool_result = await tool_fn.ainvoke(tc["args"])
                messages.append(ToolMessage(content=tool_result, tool_call_id=tc["id"]))

    return {"error": f"agent did not converge for package '{package_name}'"}


async def analyze(state: RegistryState) -> dict:
    direct_deps = state.get("direct_dependencies", [])
    if not direct_deps:
        return {"result_id": None}

    llm_with_tools = _llm.bind_tools(_TOOLS)

    # first saved package ID (fallback primary)
    first_result_id: str | None = None
    # ID of first package whose repository_url maps to a GitHub owner
    primary_result_id: str | None = None

    for dep in direct_deps:
        pkg_name = dep["name"]
        version_spec = dep.get("version_spec", "")
        current_version = _strip_version_range(version_spec)

        try:
            info = await _fetch_package_info(llm_with_tools, pkg_name)
        except Exception as exc:
            _log.warning("registry.analyze: failed to fetch %s: %s", pkg_name, exc)
            entry = RegistryEntry(name=pkg_name, current_version=current_version)
            saved_id = await registry_dao.save(entry)
            if first_result_id is None:
                first_result_id = saved_id
            continue

        if info.get("error"):
            _log.warning("registry.analyze: error for %s: %s", pkg_name, info["error"])
            entry = RegistryEntry(name=pkg_name, current_version=current_version)
            saved_id = await registry_dao.save(entry)
            if first_result_id is None:
                first_result_id = saved_id
            continue

        # Save to npm cache only on fresh fetches
        if not info.get("from_cache"):
            try:
                cache_doc = {
                    k: info.get(k)
                    for k in NpmPackageCache.model_fields
                    if k
                    not in (
                        "name",
                        "fetched_at",
                        "author",
                        "contributors",
                        "maintainers",
                        "dist",
                    )
                }
                cache_entry = NpmPackageCache(
                    name=pkg_name,
                    fetched_at=datetime.now(UTC),
                    author=_parse_author(info.get("author")),
                    contributors=_parse_persons(info.get("contributors")),
                    maintainers=_parse_persons(info.get("maintainers")),
                    dist=(
                        NpmDist(**info["dist"])
                        if isinstance(info.get("dist"), dict)
                        else None
                    ),
                    **cache_doc,
                )
                await npm_cache_dao.upsert_cached_package(cache_entry)
            except Exception:
                _log.warning("registry.analyze: cache write failed for '%s'", pkg_name)

        pkg = _build_package_record(pkg_name, current_version, info)
        owner, repo_name = _parse_repository_owner_name(info.get("repository_url"))
        entry = RegistryEntry(
            **pkg.model_dump(),
            repository_owner=owner,
            repository_name=repo_name,
        )
        saved_id = await registry_dao.save(entry)

        if first_result_id is None:
            first_result_id = saved_id
        if owner and primary_result_id is None:
            primary_result_id = saved_id

    result_id = primary_result_id or first_result_id
    _log.info(
        "registry.analyze: saved %d packages, result_id=%s",
        len(direct_deps),
        result_id,
    )
    return {"result_id": result_id}
