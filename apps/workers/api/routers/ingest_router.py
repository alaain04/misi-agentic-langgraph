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
    unknown = [t for t in body.entity_types if t not in registry]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown entity_type(s) {unknown}, must be one of {sorted(registry)}",
        )
    try:
        job_ids = {
            entity_type: await ingest_service.create_job(entity_type, body.items)
            for entity_type in body.entity_types
        }
    except Exception:
        raise HTTPException(status_code=503, detail="failed to enqueue job") from None
    return IngestResponse(job_ids=job_ids)


@router.get("/status/{job_id}", response_model=StatusResponse)
async def get_status(
    job_id: str,
    ingest_service: IngestService = Depends(get_ingest_service),
) -> StatusResponse:
    status = await ingest_service.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return StatusResponse.model_validate(status)
