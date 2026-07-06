import logging
from datetime import UTC, datetime

from src.db.connection import get_db
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.models.job import Job, JobStatus

logger = logging.getLogger(__name__)


class JobDAO(JobRepositoryPort):
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

    async def mark_failed(self, job_id: str, error: str | None = None) -> None:
        fields: dict = {"status": JobStatus.failed, "completed_at": datetime.now(UTC)}
        if error:
            fields["error"] = error
        await self._col.update_one({"_id": job_id}, {"$set": fields})

    async def mark_cancelled(self, job_id: str) -> None:
        await self._col.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": JobStatus.cancelled,
                    "completed_at": datetime.now(UTC),
                }
            },
        )

    async def start_artifact(self, job_id: str, node: str) -> None:
        """Insert or update an artifact entry as 'running'."""
        now = datetime.now(UTC)
        artifact = {
            "node": node,
            "status": "running",
            "started_at": now,
            "completed_at": None,
        }
        result = await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {
                "$set": {
                    "artifacts.$.status": "running",
                    "artifacts.$.started_at": now,
                    "artifacts.$.completed_at": None,
                }
            },
        )
        if result.matched_count == 0:
            await self._col.update_one(
                {"_id": job_id},
                {"$push": {"artifacts": artifact}},
            )

    async def complete_artifact(self, job_id: str, node: str, status: str) -> None:
        """Mark an artifact as done or failed. Creates entry if missing."""
        now = datetime.now(UTC)
        result = await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {"$set": {"artifacts.$.status": status, "artifacts.$.completed_at": now}},
        )
        if result.matched_count == 0:
            await self._col.update_one(
                {"_id": job_id},
                {
                    "$push": {
                        "artifacts": {
                            "node": node,
                            "status": status,
                            "started_at": now,
                            "completed_at": now,
                        }
                    }
                },
            )

    async def push_artifact_message(self, job_id: str, node: str, message: dict) -> None:
        """Append a chat message to the artifact's messages array.

        Upserts the artifact with status 'running' if it does not exist yet.
        This handles finding_reviewer, which calls this before the runner creates its artifact.
        """
        result = await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {"$push": {"artifacts.$.messages": message}},
        )
        if result.matched_count == 0:
            now = datetime.now(UTC)
            await self._col.update_one(
                {"_id": job_id},
                {"$push": {"artifacts": {
                    "node": node,
                    "status": "running",
                    "started_at": now,
                    "completed_at": None,
                    "messages": [message],
                }}},
            )

    async def push_artifact_item(self, job_id: str, node: str, field: str, item: dict) -> None:
        """Append an item to an array field inside an existing artifact entry."""
        await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {"$push": {f"artifacts.$.{field}": item}},
        )

    async def update_artifact_data(self, job_id: str, node: str, data: dict) -> None:
        """Merge extra fields into an existing artifact entry."""
        update_fields = {f"artifacts.$.{k}": v for k, v in data.items()}
        await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {"$set": update_fields},
        )

    async def save_cost(self, job_id: str, cost: float) -> None:
        await self._col.update_one({"_id": job_id}, {"$set": {"cost": cost}})

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
