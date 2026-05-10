"""Dependency freshness — checks npm packages against their latest versions."""

import logging

from src.main_graph.subgraphs.ingestion_subgraphs.dependency_freshness.dao import (
    dependency_freshness_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.dependency_freshness.models import (
    FreshnessEntry,
    FreshnessRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.dependency_freshness.state import (
    DependencyFreshnessState,
)
from src.services.npm_cache import NpmPackageCacheEntry, get_cached
from src.services.npm_ingestor_client import ingest, wait

logger = logging.getLogger(__name__)

_MAX_PACKAGES = 60


def _parse_version(v: str) -> tuple[int, int, int]:
    cleaned = v.lstrip("v^~>=<")
    parts = cleaned.split(".")
    try:
        major = int(parts[0]) if parts else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2].split("-")[0]) if len(parts) > 2 else 0
        return major, minor, patch
    except (ValueError, IndexError):
        return 0, 0, 0


def _extract_npm_packages(sbom: dict) -> list[tuple[str, str]]:
    results = []
    for comp in sbom.get("components", []):
        purl = comp.get("purl", "")
        if purl.startswith("pkg:npm/"):
            name = comp.get("name", "")
            version = comp.get("version", "unknown")
            if name:
                results.append((name, version))
    return results[:_MAX_PACKAGES]


def _build_record(
    name: str,
    current_version: str,
    entry: NpmPackageCacheEntry | None,
) -> FreshnessRecord:
    if not entry or not entry.registry_data:
        return FreshnessRecord(name=name, current_version=current_version)

    meta = entry.registry_data
    latest = meta.get("dist-tags", {}).get("latest")
    versions = meta.get("versions", {})
    flags: list[str] = []

    current_info = versions.get(current_version, {})
    if current_info.get("deprecated"):
        flags.append("deprecated")

    major_behind = 0
    minor_behind = 0

    if latest and latest != current_version:
        cur = _parse_version(current_version)
        lat = _parse_version(latest)
        if lat > cur:
            major_behind = lat[0] - cur[0]
            if major_behind > 0:
                flags.append("major-outdated")
            elif lat[1] - cur[1] > 0:
                minor_behind = lat[1] - cur[1]
                flags.append("minor-outdated")
            else:
                flags.append("patch-outdated")

    return FreshnessRecord(
        name=name,
        current_version=current_version,
        latest_version=latest,
        major_behind=major_behind,
        minor_behind=minor_behind,
        is_deprecated=bool(current_info.get("deprecated")),
        flags=flags,
    )


async def analyze(state: DependencyFreshnessState) -> dict:
    sbom = state.get("sbom_cyclonedx", {})
    concern = state.get("concern", "")
    packages = _extract_npm_packages(sbom)

    if not packages:
        logger.warning("dependency_freshness: no npm packages in SBOM")
        entry = FreshnessEntry(
            records=[],
            outdated_count=0,
            major_outdated_count=0,
            concern=concern,
        )
        result_id = await dependency_freshness_dao.save(entry)
        return {"result_id": result_id}

    names = [name for name, _ in packages]
    job_id = await ingest("npm", names)
    await wait(job_id)

    records = [
        _build_record(name, version, await get_cached(name))
        for name, version in packages
    ]
    outdated = sum(1 for r in records if r.flags)
    major_outdated = sum(1 for r in records if r.major_behind > 0)

    entry = FreshnessEntry(
        records=records,
        outdated_count=outdated,
        major_outdated_count=major_outdated,
        concern=concern,
    )
    result_id = await dependency_freshness_dao.save(entry)
    logger.info(
        "dependency_freshness: %d packages, %d outdated (%d major), result_id=%s",
        len(records),
        outdated,
        major_outdated,
        result_id,
    )
    return {"result_id": result_id}
