from datetime import UTC, datetime

from pymongo.asynchronous.database import AsyncDatabase

from domain.ports.job_repository_port import JobRepositoryPort


class MongoJobRepository(JobRepositoryPort):
    def __init__(self, db: AsyncDatabase) -> None:
        self._col = db["ingest_jobs"]

    async def create(self, job_id: str, packages: list[str]) -> None:
        now = datetime.now(UTC)
        await self._col.insert_one(
            {
                "_id": job_id,
                "packages": packages,
                "total": len(packages),
                "completed": 0,
                "failed": 0,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            }
        )

    async def record_success(self, job_id: str) -> None:
        await self._record(job_id, "completed")

    async def record_failure(self, job_id: str) -> None:
        await self._record(job_id, "failed")

    async def _record(self, job_id: str, field: str) -> None:
        await self._col.find_one_and_update(
            {"_id": job_id},
            {
                "$inc": {field: 1},
                "$set": {"status": "running", "updated_at": datetime.now(UTC)},
            },
        )
        await self._col.update_one(
            {
                "_id": job_id,
                "$expr": {"$gte": [{"$add": ["$completed", "$failed"]}, "$total"]},
            },
            {"$set": {"status": "done", "updated_at": datetime.now(UTC)}},
        )

    async def get_status(self, job_id: str) -> dict | None:
        doc = await self._col.find_one(
            {"_id": job_id},
            {"_id": 0, "status": 1, "total": 1, "completed": 1, "failed": 1},
        )
        if not doc:
            return None
        return {"job_id": job_id, **doc}

    async def delete(self, job_id: str) -> None:
        await self._col.delete_one({"_id": job_id})
