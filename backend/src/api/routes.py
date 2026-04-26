import asyncio
import math
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.graphs.main_graph.constants import (
    ORCHESTRATOR,
    PROJECT_DISCOVERY,
    RECOMMENDER,
    REVIEWER,
    SUMMARIZER,
)
from src.models.job import Job, JobMetadata, JobStatus
from src.services.job_dao import JobDAO
from src.services.job_runner import resume_analysis, run_analysis

router = APIRouter()

LockFileName = Literal["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]

_KNOWN_SUBGRAPHS: frozenset[str] = frozenset(
    ["registry", "repo", "runtime", "risk_score", "recommendation"]
)


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


def build_graph_info(job: Job) -> GraphInfo:
    result = job.result or {}
    plan: list[str] = result.get("plan") or []
    artifact_nodes: list[str] = [a["node"] for a in (job.artifacts or [])]

    seen: set[str] = set()
    subgraph_nodes: list[str] = []
    for s in plan:
        if s in _KNOWN_SUBGRAPHS and s not in seen:
            seen.add(s)
            subgraph_nodes.append(s)
    for s in artifact_nodes:
        if s in _KNOWN_SUBGRAPHS and s not in seen:
            seen.add(s)
            subgraph_nodes.append(s)

    nodes: list[GraphNodeInfo] = [
        GraphNodeInfo(id="START", type="terminal", order=0),
        GraphNodeInfo(id=PROJECT_DISCOVERY, type="backbone", order=1),
        GraphNodeInfo(id=ORCHESTRATOR, type="backbone", order=2),
        *[GraphNodeInfo(id=s, type="subgraph", order=3) for s in subgraph_nodes],
        GraphNodeInfo(id=SUMMARIZER, type="backbone", order=4),
        GraphNodeInfo(id=REVIEWER, type="backbone", order=5),
        GraphNodeInfo(id=RECOMMENDER, type="backbone", order=6),
        GraphNodeInfo(id="END", type="terminal", order=7),
    ]

    edges: list[GraphEdgeInfo] = [
        GraphEdgeInfo(source="START", target=PROJECT_DISCOVERY),
        GraphEdgeInfo(source=PROJECT_DISCOVERY, target=ORCHESTRATOR),
    ]
    if subgraph_nodes:
        for s in subgraph_nodes:
            edges.append(GraphEdgeInfo(source=ORCHESTRATOR, target=s))
            edges.append(GraphEdgeInfo(source=s, target=SUMMARIZER))
    else:
        edges.append(GraphEdgeInfo(source=ORCHESTRATOR, target=SUMMARIZER))
    edges += [
        GraphEdgeInfo(source=SUMMARIZER, target=REVIEWER),
        GraphEdgeInfo(source=REVIEWER, target=RECOMMENDER),
        GraphEdgeInfo(source=RECOMMENDER, target="END"),
    ]

    return GraphInfo(nodes=nodes, edges=edges)


class AnalysisMetadata(BaseModel):
    package_json: str
    lock_file: str
    lock_file_name: LockFileName
    concern: str


class AnalysisRequest(BaseModel):
    metadata: AnalysisMetadata


class AnalysisStatusResponse(BaseModel):
    trace_id: str
    status: JobStatus
    metadata: JobMetadata
    completed_at: datetime | None = None
    results: dict | None = None
    artifacts: list[dict] = []
    assistant_message: str | None = None
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


@router.post("/analyze", status_code=202)
async def analyze(request: AnalysisRequest):
    job = Job(metadata=JobMetadata(**request.metadata.model_dump()))

    dao = JobDAO()
    await dao.create(job)

    asyncio.create_task(
        run_analysis(
            job_id=job.id,
            package_json=job.metadata.package_json,
            lock_file=job.metadata.lock_file,
            lock_file_name=job.metadata.lock_file_name,
            concern=job.metadata.concern,
        )
    )

    return {"trace_id": job.id, "status": job.status}


@router.get("/analyze/{trace_id}", response_model=AnalysisStatusResponse)
async def get_analysis_status(trace_id: str):
    dao = JobDAO()
    job = await dao.get(trace_id)

    if job is None:
        raise HTTPException(status_code=404, detail="trace_id not found")

    return AnalysisStatusResponse(
        trace_id=job.id,
        status=job.status,
        metadata=job.metadata,
        completed_at=job.completed_at,
        results=job.result,
        artifacts=job.artifacts,
        assistant_message=job.assistant_message,
        graph=build_graph_info(job),
    )


@router.post("/analyze/{trace_id}/chat", status_code=202)
async def chat(trace_id: str, request: ChatRequest):
    dao = JobDAO()
    job = await dao.get(trace_id)
    if job is None:
        raise HTTPException(status_code=404, detail="trace_id not found")
    if job.status != JobStatus.awaiting_approval:
        raise HTTPException(
            status_code=409,
            detail=f"Job is not awaiting user input (status: {job.status})",
        )
    asyncio.create_task(resume_analysis(job_id=trace_id, user_message=request.message))
    return {"trace_id": trace_id, "status": JobStatus.running}


@router.get("/jobs", response_model=JobsListResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status: JobStatus | None = Query(None),
    trace_id: str | None = Query(None),
):
    dao = JobDAO()
    jobs, total = await dao.list(page, limit, status=status, trace_id=trace_id)
    pages = math.ceil(total / limit) if total > 0 else 1
    items = [
        JobListItem(
            trace_id=j.id,
            status=j.status,
            concern=j.metadata.concern,
            created_at=j.created_at,
            completed_at=j.completed_at,
        )
        for j in jobs
    ]
    return JobsListResponse(
        items=items, total=total, page=page, limit=limit, pages=pages
    )
