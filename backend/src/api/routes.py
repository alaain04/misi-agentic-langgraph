import asyncio
import math
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.models.job import Job, JobStatus
from src.services.job_dao import JobDAO
from src.services.job_runner import resume_analysis, run_analysis

router = APIRouter()

LockFileName = Literal["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]


class AnalysisRequest(BaseModel):
    package_json: str
    lock_file: str
    lock_file_name: LockFileName
    concern: str


class AnalysisStatusResponse(BaseModel):
    trace_id: str
    status: JobStatus
    concern: str
    package_json: str | None = None
    lock_file_name: str | None = None
    completed_at: datetime | None = None
    results: dict | None = None
    artifacts: list[dict] = []
    assistant_message: str | None = None


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
    job = Job(
        package_json=request.package_json,
        lock_file=request.lock_file,
        lock_file_name=request.lock_file_name,
        concern=request.concern,
    )

    dao = JobDAO()
    await dao.create(job)

    asyncio.create_task(
        run_analysis(
            job_id=job.id,
            package_json=job.package_json,
            lock_file=job.lock_file,
            lock_file_name=job.lock_file_name,
            concern=job.concern,
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
        concern=job.concern,
        package_json=job.package_json,
        lock_file_name=job.lock_file_name,
        completed_at=job.completed_at,
        results=job.result,
        artifacts=job.artifacts,
        assistant_message=job.assistant_message,
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
            concern=j.concern,
            created_at=j.created_at,
            completed_at=j.completed_at,
        )
        for j in jobs
    ]
    return JobsListResponse(
        items=items, total=total, page=page, limit=limit, pages=pages
    )
