from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_fetcher_registry, get_ingest_service
from api.schemas import IngestRequest, IngestResponse, StatusResponse
from domain.ports.fetcher_port import FetcherPort
from services.application_services.ingest_service import IngestService

router = APIRouter(tags=["ingest"])


@router.post("/ingest", status_code=201, response_model=IngestResponse)
async def ingest(
    body: IngestRequest,
    ingest_service: IngestService = Depends(get_ingest_service),
    registry: dict[str, FetcherPort] = Depends(get_fetcher_registry),
) -> IngestResponse:
    if body.entity_type not in registry:
        raise HTTPException(
            status_code=422,
            detail=f"unknown entity_type {body.entity_type!r}, must be one of {sorted(registry)}",
        )
    try:
        job_id = await ingest_service.create_job(body.entity_type, body.items)
    except Exception:
        raise HTTPException(status_code=503, detail="failed to enqueue job")
    return IngestResponse(job_id=job_id)


@router.get("/status/{job_id}", response_model=StatusResponse)
async def get_status(
    job_id: str,
    ingest_service: IngestService = Depends(get_ingest_service),
) -> StatusResponse:
    status = await ingest_service.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return StatusResponse(**status)
