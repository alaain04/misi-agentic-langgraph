from datetime import UTC, datetime, timedelta

from bson import ObjectId

from src.db.connection import get_db
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.ingestion_subgraphs.repo.models import (
    RepoCacheEntry,
    RepoEntry,
)


class RepoDAO(IngestionResultPort):
    @property
    def _col(self):
        return get_db()["repositories"]

    async def save(self, entry: RepoEntry) -> str:
        result = await self._col.insert_one(entry.model_dump())
        return str(result.inserted_id)

    async def get(self, doc_id: str) -> dict | None:
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc


repo_dao = RepoDAO()


class RepoCacheDAO:
    @property
    def _col(self):
        return get_db()["repo_cache"]

    async def find_cached_entry(
        self, owner: str, repo_name: str, lookback_days: int, max_age_days: int
    ) -> RepoCacheEntry | None:
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        doc = await self._col.find_one(
            {
                "owner": owner,
                "repo_name": repo_name,
                "lookback_days": lookback_days,
                "fetched_at": {"$gte": cutoff},
            }
        )
        if doc is None:
            return None
        doc.pop("_id", None)
        return RepoCacheEntry(**doc)

    async def upsert_cached_entry(self, data: RepoCacheEntry) -> None:
        await self._col.replace_one(
            {
                "owner": data.owner,
                "repo_name": data.repo_name,
                "lookback_days": data.lookback_days,
            },
            data.model_dump(),
            upsert=True,
        )


repo_cache_dao = RepoCacheDAO()
