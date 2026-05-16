import json
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src import fetchers, jobs
from src.nats_client import get_js, subject_for

router = APIRouter()


class IngestRequest(BaseModel):
    entity_type: str
    items: Annotated[list[str], Field(min_length=1)]

    def validate_entity_type(self) -> None:
        known = set(fetchers.entity_types())
        if self.entity_type not in known:
            raise HTTPException(
                status_code=422,
                detail=f"unknown entity_type {self.entity_type!r},"
                f" must be one of {sorted(known)}",
            )


class IngestResponse(BaseModel):
    job_id: str


class StatusResponse(BaseModel):
    job_id: str
    status: str
    total: int
    completed: int
    failed: int


@router.post("/ingest", status_code=201, response_model=IngestResponse)
async def ingest(body: IngestRequest) -> IngestResponse:
    body.validate_entity_type()
    job_id = str(uuid.uuid4())
    await jobs.create(job_id, body.items)
    js = get_js()
    subject = subject_for(body.entity_type)
    try:
        for name in body.items:
            payload = json.dumps(
                {"job_id": job_id, "entity_type": body.entity_type, "name": name}
            ).encode()
            await js.publish(subject, payload)
    except Exception:
        await jobs.delete(job_id)
        raise HTTPException(status_code=503, detail="failed to enqueue job")
    return IngestResponse(job_id=job_id)


@router.get("/status/{job_id}", response_model=StatusResponse)
async def get_status(job_id: str) -> StatusResponse:
    status = await jobs.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return StatusResponse(**status)
