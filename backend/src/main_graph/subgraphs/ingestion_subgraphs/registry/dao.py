from datetime import UTC, datetime, timedelta

from bson import ObjectId

from src.db.connection import get_db
from src.main_graph.subgraphs.ingestion_subgraphs.registry.models import (
    NpmPackageCache,
    RegistryEntry,
)


class RegistryDAO:
    @property
    def _col(self):
        return get_db()["registries"]

    async def save(self, entry: RegistryEntry) -> str:
        result = await self._col.insert_one(entry.model_dump())
        return str(result.inserted_id)

    async def get(self, doc_id: str) -> dict | None:
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc


registry_dao = RegistryDAO()


class NpmCacheDAO:
    @property
    def _col(self):
        return get_db()["npm_package_cache"]

    async def find_cached_package(
        self, name: str, max_age_days: int
    ) -> NpmPackageCache | None:
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        doc = await self._col.find_one(
            {"name": name, "fetched_at": {"$gte": cutoff}}
        )
        if doc is None:
            return None
        doc.pop("_id", None)
        return NpmPackageCache(**doc)

    async def upsert_cached_package(self, data: NpmPackageCache) -> None:
        await self._col.replace_one(
            {"name": data.name},
            data.model_dump(),
            upsert=True,
        )


npm_cache_dao = NpmCacheDAO()
