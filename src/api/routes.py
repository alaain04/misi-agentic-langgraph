import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

from src.models.job import Job
from src.services.job_dao import JobDAO
from src.services.job_runner import run_discovery

router = APIRouter()


class AnalysisRequest(BaseModel):
    repo_url: HttpUrl
    token: str | None = None
    concern: str


@router.post("/analyze", status_code=202)
async def analyze(request: AnalysisRequest):
    job = Job(
        repo_url=str(request.repo_url),
        token=request.token,
        concern=request.concern,
    )

    dao = JobDAO()
    await dao.create(job)

    asyncio.create_task(
        run_discovery(
            job_id=job.id,
            repo_url=job.repo_url,
            concern=job.concern,
            token=job.token,
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
