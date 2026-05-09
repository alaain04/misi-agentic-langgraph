from __future__ import annotations

from pydantic import BaseModel, Field


class SupplyChainRecord(BaseModel):
    name: str
    version: str
    risk_score: float = 0.0
    last_publish_days: int | None = None
    weekly_downloads: int | None = None
    flags: list[str] = Field(default_factory=list)


class SupplyChainEntry(BaseModel):
    """Stored per analysis run in the `supply_chain` collection."""

    records: list[SupplyChainRecord] = Field(default_factory=list)
    high_risk_count: int = 0
    concern: str = ""
