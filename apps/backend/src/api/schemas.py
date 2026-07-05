from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.models.job import JobMetadata, JobStatus


class GraphNodeInfo(BaseModel):
    id: str
    type: Literal["terminal", "backbone", "subgraph"]
    order: int


class GraphEdgeInfo(BaseModel):
    source: str
    target: str


class GraphInfo(BaseModel):
    nodes: list[GraphNodeInfo]
    edges: list[GraphEdgeInfo]


class AnalysisRequest(BaseModel):
    repo_url: str
    concern: str


class AnalysisStatusResponse(BaseModel):
    trace_id: str
    status: JobStatus
    metadata: JobMetadata
    completed_at: datetime | None = None
    results: dict | None = None
    error: str | None = None
    artifacts: list[dict] = []
    graph: GraphInfo


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
