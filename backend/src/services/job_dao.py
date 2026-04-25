from datetime import UTC, datetime

from src.db.connection import get_db
from src.models.job import Job, JobStatus


class JobDAO:
    def __init__(self):
        self._col = get_db()["jobs"]

    async def create(self, job: Job) -> Job:
        await self._col.insert_one(job.to_doc())
        return job

    async def get(self, job_id: str) -> Job | None:
        doc = await self._col.find_one({"_id": job_id})
        if doc is None:
            return None
        doc["id"] = doc.pop("_id")
        return Job(**doc)

    async def update_status(self, job_id: str, status: JobStatus) -> None:
        await self._col.update_one({"_id": job_id}, {"$set": {"status": status}})

    async def save_result(self, job_id: str, result: dict) -> None:
        await self._col.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": JobStatus.done,
                    "result": result,
                    "completed_at": datetime.now(UTC),
                }
            },
        )

    async def mark_failed(self, job_id: str) -> None:
        await self._col.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": JobStatus.failed,
                    "completed_at": datetime.now(UTC),
                }
            },
        )

    async def get_pending(self) -> list[Job]:
        cursor = self._col.find({"status": JobStatus.pending})
        jobs = []
        async for doc in cursor:
            doc["id"] = doc.pop("_id")
            jobs.append(Job(**doc))
        return jobs

    async def list(
        self,
        page: int = 1,
        limit: int = 10,
        status: JobStatus | None = None,
        trace_id: str | None = None,
    ) -> tuple[list[Job], int]:
        query: dict = {}
        if status is not None:
            query["status"] = status
        if trace_id is not None:
            query["_id"] = {"$regex": trace_id, "$options": "i"}
        total = await self._col.count_documents(query)
        skip = (page - 1) * limit
        cursor = self._col.find(query).sort("created_at", -1).skip(skip).limit(limit)
        jobs = []
        async for doc in cursor:
            doc["id"] = doc.pop("_id")
            jobs.append(Job(**doc))
        return jobs, total
