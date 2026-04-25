from datetime import UTC, datetime
from enum import StrEnum

from bson import ObjectId
from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    awaiting_approval = "awaiting_approval"
    done = "done"
    failed = "failed"


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(ObjectId()))
    package_json: str | None = None
    lock_file: str | None = None
    lock_file_name: str | None = None
    concern: str
    status: JobStatus = JobStatus.pending
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    result: dict | None = None
    artifacts: list[dict] = Field(default_factory=list)

    def to_doc(self) -> dict:
        doc = self.model_dump()
        doc["_id"] = doc.pop("id")
        return doc
