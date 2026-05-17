from typing import Annotated

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    entity_types: Annotated[list[str], Field(min_length=1)]
    items: Annotated[list[str], Field(min_length=1)]


class IngestResponse(BaseModel):
    job_ids: dict[str, str]


class StatusResponse(BaseModel):
    job_id: str
    status: str
    total: int
    completed: int
    failed: int
