from __future__ import annotations

from pydantic import BaseModel, Field


class FreshnessRecord(BaseModel):
    name: str
    current_version: str
    latest_version: str | None = None
    major_behind: int = 0
    minor_behind: int = 0
    is_deprecated: bool = False
    flags: list[str] = Field(default_factory=list)


class FreshnessEntry(BaseModel):
    """Stored per analysis run in the `dependency_freshness` collection."""

    records: list[FreshnessRecord] = Field(default_factory=list)
    outdated_count: int = 0
    major_outdated_count: int = 0
    concern: str = ""
