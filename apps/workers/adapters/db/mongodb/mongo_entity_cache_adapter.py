from datetime import UTC, datetime

from pymongo.asynchronous.database import AsyncDatabase

from domain.ports.entity_cache_port import EntityCachePort


class MongoEntityCacheAdapter(EntityCachePort):
    def __init__(self, db: AsyncDatabase[dict[str, object]]) -> None:
        self._db = db

    async def save(self, collection: str, name: str, doc: dict[str, object]) -> None:
        await self._db[collection].replace_one(
            {"name": name},
            {"name": name, "fetched_at": datetime.now(UTC), **doc},
            upsert=True,
        )

    async def get(self, collection: str, name: str) -> dict[str, object] | None:
        return await self._db[collection].find_one({"name": name}, {"_id": 0})
