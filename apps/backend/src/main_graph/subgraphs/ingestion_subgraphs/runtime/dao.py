from datetime import UTC, datetime, timedelta

from bson import ObjectId

from src.db.connection import get_db
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.models import (
    RuntimeCacheEntry,
    RuntimeEntry,
)


class RuntimeDAO(IngestionResultPort):
    @property
    def _col(self):
        return get_db()["runtime_results"]

    async def save(self, entry: RuntimeEntry) -> str:
        result = await self._col.insert_one(entry.model_dump())
        return str(result.inserted_id)

    async def get(self, doc_id: str) -> dict | None:
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc


runtime_dao = RuntimeDAO()


class RuntimeCacheDAO:
    @property
    def _col(self):
        return get_db()["runtime_cache"]

    async def find_cached_entry(
        self, package_name: str, package_version: str, max_age_days: int
    ) -> RuntimeCacheEntry | None:
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        doc = await self._col.find_one(
            {
                "package_name": package_name,
                "package_version": package_version,
                "fetched_at": {"$gte": cutoff},
            }
        )
        if doc is None:
            return None
        doc.pop("_id", None)
        return RuntimeCacheEntry(**doc)

    async def upsert_cached_entry(self, data: RuntimeCacheEntry) -> None:
        await self._col.replace_one(
            {
                "package_name": data.package_name,
                "package_version": data.package_version,
            },
            data.model_dump(),
            upsert=True,
        )


runtime_cache_dao = RuntimeCacheDAO()
