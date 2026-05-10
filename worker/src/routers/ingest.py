import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src import jobs
from src.nats_client import get_js, subject_for

router = APIRouter()


class IngestRequest(BaseModel):
    entity_type: str
    items: list[str]


class IngestResponse(BaseModel):
    job_id: str


class StatusResponse(BaseModel):
    job_id: str
    status: str
    total: int
    completed: int
    failed: int


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest) -> IngestResponse:
    job_id = str(uuid.uuid4())
    await jobs.create(job_id, body.items)
    js = get_js()
    subject = subject_for(body.entity_type)
    for name in body.items:
        payload = json.dumps(
            {"job_id": job_id, "entity_type": body.entity_type, "name": name}
        ).encode()
        await js.publish(subject, payload)
    return IngestResponse(job_id=job_id)


@router.get("/status/{job_id}", response_model=StatusResponse)
async def get_status(job_id: str) -> StatusResponse:
    status = await jobs.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return StatusResponse(**status)
