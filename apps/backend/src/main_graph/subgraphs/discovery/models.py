from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SbomEntry(BaseModel):
    """Stored per analysis run in the `sbom_gens` collection."""

    repo_url: str = ""
    sbom_cyclonedx: dict[str, Any] = Field(default_factory=dict)
    scan_error: str | None = None
