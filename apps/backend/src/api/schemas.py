from datetime import datetime

from pydantic import BaseModel

from src.models.job import JobMetadata, JobStatus


class AnalysisRequest(BaseModel):
    repo_url: str
    concern: str
    autopilot: bool = False


class AnalysisStatusResponse(BaseModel):
    trace_id: str
    status: JobStatus
    metadata: JobMetadata
    completed_at: datetime | None = None
    results: dict | None = None
    error: str | None = None
    artifacts: list[dict] = []
    cost: float | None = None


class ChatRequest(BaseModel):
    message: str


class JobListItem(BaseModel):
    trace_id: str
    status: JobStatus
    concern: str
    created_at: datetime
    completed_at: datetime | None = None


class JobsListResponse(BaseModel):
    items: list[JobListItem]
    total: int
    page: int
    limit: int
    pages: int
