from datetime import UTC, datetime

from pymongo.asynchronous.collection import AsyncCollection

from src.db import get_db

_COLLECTION = "ingest_jobs"


def _col() -> AsyncCollection:
    return get_db()[_COLLECTION]


async def create(job_id: str, packages: list[str]) -> None:
    now = datetime.now(UTC)
    await _col().insert_one(
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


async def _record(job_id: str, field: str) -> None:
    await _col().find_one_and_update(
        {"_id": job_id},
        {
            "$inc": {field: 1},
            "$set": {"status": "running", "updated_at": datetime.now(UTC)},
        },
    )
    # Atomic conditional flip: only sets done if completed+failed >= total
    await _col().update_one(
        {
            "_id": job_id,
            "$expr": {"$gte": [{"$add": ["$completed", "$failed"]}, "$total"]},
        },
        {"$set": {"status": "done", "updated_at": datetime.now(UTC)}},
    )


async def record_success(job_id: str) -> None:
    await _record(job_id, "completed")


async def record_failure(job_id: str) -> None:
    await _record(job_id, "failed")


async def get_status(job_id: str) -> dict | None:
    doc = await _col().find_one(
        {"_id": job_id},
        {"_id": 0, "status": 1, "total": 1, "completed": 1, "failed": 1},
    )
    if not doc:
        return None
    return {
        "job_id": job_id,
        "status": doc["status"],
        "total": doc["total"],
        "completed": doc["completed"],
        "failed": doc["failed"],
    }
