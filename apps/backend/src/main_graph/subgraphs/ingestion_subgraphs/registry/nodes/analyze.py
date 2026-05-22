"""Analyze node for the Registry subgraph.

Triggers npm metadata ingestion via the workers service, then reads the
result from the shared npm_package_cache MongoDB collection.
"""

from __future__ import annotations

import logging

from src.db.connection import get_db
from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import registry_dao
from src.main_graph.subgraphs.ingestion_subgraphs.registry.models import RegistryEntry
from src.main_graph.subgraphs.ingestion_subgraphs.registry.state import RegistryState
from src.utils.workers_client import ingest_and_wait

_log = logging.getLogger(__name__)


def _extract_entry(dep_name: str, doc: dict) -> RegistryEntry:
    """Extract RegistryEntry fields from an npm_package_cache document.

    The workers npm fetcher stores the full npm registry response.
    Fields: doc["time"]["modified"], doc["dist-tags"]["latest"],
    doc["deprecated"], doc["maintainers"], doc["downloads"]["weekly"]
    (downloads may be a nested object if fetched separately).
    Adjust field paths here if the workers cache schema differs.
    """
    time_data = doc.get("time") or {}
    last_publish = time_data.get("modified")

    downloads = doc.get("downloads") or {}
    weekly_downloads: int | None = None
    if isinstance(downloads, dict):
        weekly_downloads = downloads.get("weekly") or downloads.get("last-week")
    elif isinstance(downloads, int):
        weekly_downloads = downloads

    deprecated = doc.get("deprecated")
    is_deprecated = bool(deprecated)

    maintainers = doc.get("maintainers") or []
    maintainers_count = len(maintainers) if isinstance(maintainers, list) else None

    return RegistryEntry(
        dep_name=dep_name,
        last_publish=last_publish,
        weekly_downloads=weekly_downloads,
        is_deprecated=is_deprecated,
        maintainers_count=maintainers_count,
    )


async def analyze(state: RegistryState) -> dict:
    dep_name = state.get("dependency_name", "")
    if not dep_name:
        result_id = await registry_dao.save(RegistryEntry())
        return {"result_id": result_id}

    npm_cache = get_db()["npm_package_cache"]

    # Check cache before triggering workers
    cached_doc = await npm_cache.find_one({"name": dep_name})
    if cached_doc is None:
        try:
            await ingest_and_wait(["npm"], [dep_name])
        except Exception as exc:
            _log.warning("registry.analyze: workers ingest failed for %s: %s", dep_name, exc)
            result_id = await registry_dao.save(RegistryEntry(dep_name=dep_name))
            return {"result_id": result_id}
        cached_doc = await npm_cache.find_one({"name": dep_name})

    if cached_doc is None:
        _log.warning("registry.analyze: no npm data found for %s after ingest", dep_name)
        result_id = await registry_dao.save(RegistryEntry(dep_name=dep_name))
        return {"result_id": result_id}

    entry = _extract_entry(dep_name, cached_doc)
    result_id = await registry_dao.save(entry)
    _log.info("registry.analyze: saved %s, result_id=%s", dep_name, result_id)
    return {"result_id": result_id}
