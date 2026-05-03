import asyncio
import math
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.main_graph.constants import (
    DISCOVERY,
    ORCHESTRATOR,
    RECOMMENDER,
    REVIEWER,
    SUMMARIZER,
)
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_DEPENDENCIES
from src.main_graph.utils.dependency_resolver import resolve_execution_stages
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
    if not plan:
        orch = next(
            (a for a in (job.artifacts or []) if a["node"] == "orchestrator"), None
        )
        if orch:
            proposals = orch.get("proposals") or []
            if proposals:
                plan = proposals[-1].get("plan") or []
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

    if subgraph_nodes:
        ingestion_plan = [s for s in subgraph_nodes if s in SUBGRAPH_DEPENDENCIES]
        other_nodes = [s for s in subgraph_nodes if s not in SUBGRAPH_DEPENDENCIES]
        stages = (
            resolve_execution_stages(ingestion_plan, SUBGRAPH_DEPENDENCIES)
            if ingestion_plan
            else []
        )
        if other_nodes:
            stages.append(other_nodes)
    else:
        stages = []

    num_stages = len(stages)

    sg_order: dict[str, int] = {}
    for stage_idx, stage in enumerate(stages):
        for sg in stage:
            sg_order[sg] = 3 + stage_idx

    summarizer_order = 3 + max(num_stages, 1)
    reviewer_order = summarizer_order + 1
    recommender_order = summarizer_order + 2
    end_order = summarizer_order + 3

    nodes: list[GraphNodeInfo] = [
        GraphNodeInfo(id="START", type="terminal", order=0),
        GraphNodeInfo(id=DISCOVERY, type="backbone", order=1),
        GraphNodeInfo(id=ORCHESTRATOR, type="backbone", order=2),
        *[
            GraphNodeInfo(id=s, type="subgraph", order=sg_order[s])
            for s in subgraph_nodes
        ],
        GraphNodeInfo(id=SUMMARIZER, type="backbone", order=summarizer_order),
        GraphNodeInfo(id=REVIEWER, type="backbone", order=reviewer_order),
        GraphNodeInfo(id=RECOMMENDER, type="backbone", order=recommender_order),
        GraphNodeInfo(id="END", type="terminal", order=end_order),
    ]

    plan_set = set(subgraph_nodes)
    sg_deps_in_plan: dict[str, list[str]] = {
        sg: [d for d in SUBGRAPH_DEPENDENCIES.get(sg, []) if d in plan_set]
        for sg in subgraph_nodes
    }

    edges: list[GraphEdgeInfo] = [
        GraphEdgeInfo(source="START", target=DISCOVERY),
        GraphEdgeInfo(source=DISCOVERY, target=ORCHESTRATOR),
    ]
    if subgraph_nodes:
        for s in subgraph_nodes:
            deps = sg_deps_in_plan.get(s, [])
            if deps:
                for dep in deps:
                    edges.append(GraphEdgeInfo(source=dep, target=s))
            else:
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
