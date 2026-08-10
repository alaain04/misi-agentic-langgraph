import asyncio
import math

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.dependencies import get_job_repo
from src.api.schemas import (
    AnalysisRequest,
    AnalysisStatusResponse,
    ChatRequest,
    DepTreeResponse,
    JobListItem,
    JobsListResponse,
)
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.models.job import Job, JobMetadata, JobStatus
from src.services.job_runner import resume_analysis, run_analysis

router = APIRouter()


@router.post("/analyze", status_code=202)
async def analyze(
    request: AnalysisRequest,
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    job = Job(
        metadata=JobMetadata(
            repo_url=request.repo_url,
            concern=request.concern,
            autopilot=request.autopilot,
            used_pat=bool(request.github_token),
            remediate=request.remediate,
        )
    )
    await dao.create(job)
    asyncio.create_task(
        run_analysis(
            job_id=job.id,
            repo_url=job.metadata.repo_url,
            concern=job.metadata.concern,
            autopilot=request.autopilot,
            dao=dao,
            github_token=request.github_token,
            remediate=request.remediate,
        )
    )
    return {"trace_id": job.id, "status": job.status}


@router.get("/analyze/{trace_id}", response_model=AnalysisStatusResponse)
async def get_analysis_status(
    trace_id: str,
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    job = await dao.get(trace_id)
    if job is None:
        raise HTTPException(status_code=404, detail="trace_id not found")
    return AnalysisStatusResponse(
        trace_id=job.id,
        status=job.status,
        metadata=job.metadata,
        completed_at=job.completed_at,
        results=job.result,
        error=job.error,
        artifacts=job.artifacts,
        cost=job.cost,
    )


@router.get("/analyze/{trace_id}/dep-tree", response_model=DepTreeResponse)
async def get_dep_tree(
    trace_id: str,
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    tree = await dao.get_dep_tree(trace_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="dependency tree not yet available")
    return DepTreeResponse(job_id=trace_id, tree=tree)


@router.post("/analyze/{trace_id}/chat", status_code=202)
async def chat(
    trace_id: str,
    request: ChatRequest,
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    job = await dao.get(trace_id)
    if job is None:
        raise HTTPException(status_code=404, detail="trace_id not found")
    if job.status != JobStatus.awaiting_approval:
        raise HTTPException(
            status_code=409,
            detail=f"Job is not awaiting user input (status: {job.status})",
        )
    asyncio.create_task(
        resume_analysis(job_id=trace_id, user_message=request.message, dao=dao)
    )
    return {"trace_id": trace_id, "status": JobStatus.running}


@router.get("/jobs", response_model=JobsListResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status: JobStatus | None = Query(None),
    trace_id: str | None = Query(None),
    dao: JobRepositoryPort = Depends(get_job_repo),
):
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
