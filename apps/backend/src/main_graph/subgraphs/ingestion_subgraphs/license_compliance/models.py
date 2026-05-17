from __future__ import annotations

from pydantic import BaseModel, Field


class LicenseRecord(BaseModel):
    name: str
    version: str
    license: str | None = None
    is_compliant: bool = True
    risk_level: str = "low"
    notes: str | None = None


class LicenseComplianceEntry(BaseModel):
    """Stored per analysis run in the `license_compliance` collection."""

    records: list[LicenseRecord] = Field(default_factory=list)
    total_violations: int = 0
    concern: str = ""
