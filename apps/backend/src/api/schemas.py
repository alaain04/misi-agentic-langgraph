from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.models.job import JobMetadata, JobStatus


class AnalysisRequest(BaseModel):
    # Reject unknown fields (e.g. a "consent" typo for "remediate") instead
    # of silently dropping them and falling back to the field default.
    model_config = ConfigDict(extra="forbid")

    repo_url: str
    concern: str
    autopilot: bool = False
    # Per-request PAT for private-repo clone (Workstream D1). Used for this
    # job only; never persisted to JobMetadata or any other stored document.
    github_token: str | None = None
    remediate: bool = False


class AnalysisStatusResponse(BaseModel):
    trace_id: str
    status: JobStatus
    metadata: JobMetadata
    completed_at: datetime | None = None
    results: dict | None = None
    error: str | None = None
    artifacts: list[dict] = []
    cost: float | None = None
    cost_breakdown: dict | None = None


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


class DepTreeResponse(BaseModel):
    job_id: str
    tree: dict
