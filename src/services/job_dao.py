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
            {"$set": {"status": JobStatus.done, "result": result}},
        )

    async def get_pending(self) -> list[Job]:
        cursor = self._col.find({"status": JobStatus.pending})
        jobs = []
        async for doc in cursor:
            doc["id"] = doc.pop("_id")
            jobs.append(Job(**doc))
        return jobs
