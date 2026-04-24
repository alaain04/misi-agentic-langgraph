from datetime import UTC, datetime
from enum import StrEnum

from bson import ObjectId
from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(ObjectId()))
    repo_url: str
    token: str | None = None
    concern: str
    status: JobStatus = JobStatus.pending
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> dict:
        doc = self.model_dump()
        doc["_id"] = doc.pop("id")
        return doc
