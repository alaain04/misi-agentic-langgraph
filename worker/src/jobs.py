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


async def record_success(job_id: str) -> None:
    doc = await _col().find_one_and_update(
        {"_id": job_id},
        {
            "$inc": {"completed": 1},
            "$set": {"status": "running", "updated_at": datetime.now(UTC)},
        },
        return_document=True,
    )
    if doc and doc["completed"] + doc["failed"] >= doc["total"]:
        await _col().update_one(
            {"_id": job_id},
            {"$set": {"status": "done", "updated_at": datetime.now(UTC)}},
        )


async def record_failure(job_id: str) -> None:
    doc = await _col().find_one_and_update(
        {"_id": job_id},
        {
            "$inc": {"failed": 1},
            "$set": {"status": "running", "updated_at": datetime.now(UTC)},
        },
        return_document=True,
    )
    if doc and doc["completed"] + doc["failed"] >= doc["total"]:
        await _col().update_one(
            {"_id": job_id},
            {"$set": {"status": "done", "updated_at": datetime.now(UTC)}},
        )


async def get_status(job_id: str) -> dict | None:
    doc = await _col().find_one({"_id": job_id})
    if not doc:
        return None
    return {
        "job_id": job_id,
        "status": doc["status"],
        "total": doc["total"],
        "completed": doc["completed"],
        "failed": doc["failed"],
    }
