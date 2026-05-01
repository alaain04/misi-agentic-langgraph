import logging
from datetime import UTC, datetime

from src.db.connection import get_db
from src.models.job import Job, JobStatus

logger = logging.getLogger(__name__)


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

    async def push_proposal(self, job_id: str, proposal: dict) -> None:
        """Append a proposal entry to the orchestrator artifact's proposals array."""
        result = await self._col.update_one(
            {"_id": job_id, "artifacts.node": "orchestrator"},
            {"$push": {"artifacts.$.proposals": proposal}},
        )
        if result.matched_count == 0:
            logger.warning(
                "push_proposal: orchestrator artifact not found for job=%s", job_id
            )

    async def update_proposal(
        self, job_id: str, created_at: str, user_response: str, intent: str
    ) -> None:
        """Set user_response and user_intended_action on a specific
        orchestrator proposal."""
        await self._col.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "artifacts.$[orch].proposals.$[prop].user_response": user_response,
                    "artifacts.$[orch].proposals.$[prop].user_intended_action": intent,
                }
            },
            array_filters=[
                {"orch.node": "orchestrator"},
                {"prop.created_at": created_at},
            ],
        )

    async def update_artifact_data(self, job_id: str, node: str, data: dict) -> None:
        """Merge extra fields into an existing artifact entry."""
        update_fields = {f"artifacts.$.{k}": v for k, v in data.items()}
        await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {"$set": update_fields},
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
