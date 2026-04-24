import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.models.job import Job
from src.services.job_dao import JobDAO
from src.services.job_runner import run_discovery

router = APIRouter()

LockFileName = Literal["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]


class AnalysisRequest(BaseModel):
    package_json: str
    lock_file: str
    lock_file_name: LockFileName
    concern: str


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
        run_discovery(
            job_id=job.id,
            package_json=job.package_json,
            lock_file=job.lock_file,
            lock_file_name=job.lock_file_name,
            concern=job.concern,
        )
    )

    return {"trace_id": job.id, "status": job.status}


@router.get("/analyze/{trace_id}")
async def get_analysis_status(trace_id: str):
    dao = JobDAO()
    job = await dao.get(trace_id)

    if job is None:
        raise HTTPException(status_code=404, detail="trace_id not found")

    return {"trace_id": job.id, "status": job.status}
