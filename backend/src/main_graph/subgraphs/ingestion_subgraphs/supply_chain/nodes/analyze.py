"""Supply chain analysis — evaluates npm packages via registry data."""

import logging
from datetime import UTC, datetime

from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.dao import (
    supply_chain_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.models import (
    SupplyChainEntry,
    SupplyChainRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.state import (
    SupplyChainState,
)
from src.services.npm_cache import NpmPackageCacheEntry, get_cached
from src.services.npm_ingestor_client import ingest, wait

logger = logging.getLogger(__name__)

_MAX_PACKAGES = 50
_STALE_DAYS = 730
_VERY_STALE_DAYS = 1825
_LOW_DOWNLOADS = 1_000


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


def _days_since(iso_str: str) -> int | None:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return (datetime.now(UTC) - dt).days
    except Exception:
        return None


def _build_record(
    name: str,
    version: str,
    entry: NpmPackageCacheEntry | None,
) -> SupplyChainRecord:
    if not entry or not entry.registry_data:
        return SupplyChainRecord(name=name, version=version)

    meta = entry.registry_data
    downloads = entry.weekly_downloads
    flags: list[str] = []
    risk = 0.0

    latest_tag = meta.get("dist-tags", {}).get("latest", version)
    latest_info = meta.get("versions", {}).get(latest_tag, {})

    if latest_info.get("deprecated"):
        flags.append("deprecated")
        risk += 0.5

    time_data = meta.get("time", {})
    last_publish_days: int | None = None
    if latest_tag in time_data:
        last_publish_days = _days_since(time_data[latest_tag])
    elif "modified" in time_data:
        last_publish_days = _days_since(time_data["modified"])

    if last_publish_days is not None:
        if last_publish_days > _VERY_STALE_DAYS:
            flags.append("very-stale")
            risk += 0.35
        elif last_publish_days > _STALE_DAYS:
            flags.append("stale")
            risk += 0.2

    if downloads is not None and downloads < _LOW_DOWNLOADS:
        flags.append("low-downloads")
        risk += 0.2

    maintainers = meta.get("maintainers", [])
    if len(maintainers) == 1:
        flags.append("single-maintainer")
        risk += 0.15

    return SupplyChainRecord(
        name=name,
        version=version,
        risk_score=min(risk, 1.0),
        last_publish_days=last_publish_days,
        weekly_downloads=downloads,
        flags=flags,
    )


async def analyze(state: SupplyChainState) -> dict:
    sbom = state.get("sbom_cyclonedx", {})
    concern = state.get("concern", "")
    packages = _extract_npm_packages(sbom)

    if not packages:
        logger.warning("supply_chain: no npm packages found in SBOM")
        entry = SupplyChainEntry(records=[], high_risk_count=0, concern=concern)
        result_id = await supply_chain_dao.save(entry)
        return {"result_id": result_id}

    names = [name for name, _ in packages]
    job_id = await ingest("npm", names)
    await wait(job_id)

    records = [
        _build_record(name, version, await get_cached(name))
        for name, version in packages
    ]
    entry = SupplyChainEntry(
        records=records,
        high_risk_count=sum(1 for r in records if r.risk_score >= 0.7),
        concern=concern,
    )
    result_id = await supply_chain_dao.save(entry)
    logger.info(
        "supply_chain: %d packages, %d high-risk, result_id=%s",
        len(records),
        entry.high_risk_count,
        result_id,
    )
    return {"result_id": result_id}
