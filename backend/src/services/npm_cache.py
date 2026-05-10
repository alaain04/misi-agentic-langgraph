"""Read-only access to npm_package_cache populated by the npm-worker service."""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from src.db.connection import get_db

_COLLECTION = "npm_package_cache"


class NpmPackageCacheEntry(BaseModel):
    name: str
    fetched_at: datetime
    registry_data: dict
    weekly_downloads: int | None = None


async def get_cached(
    name: str, max_age_days: int = 7
) -> NpmPackageCacheEntry | None:
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    doc = await get_db()[_COLLECTION].find_one(
        {"name": name, "fetched_at": {"$gte": cutoff}}
    )
    if not doc:
        return None
    doc.pop("_id", None)
    return NpmPackageCacheEntry(**doc)
