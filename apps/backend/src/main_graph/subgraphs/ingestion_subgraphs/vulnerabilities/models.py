from __future__ import annotations

from pydantic import BaseModel, Field


class VulnerabilityFinding(BaseModel):
    cve_id: str | None = None
    severity: str = "unknown"
    description: str | None = None
    fixed_in: str | None = None


class VulnerabilityRecord(BaseModel):
    name: str
    version: str
    findings: list[VulnerabilityFinding] = Field(default_factory=list)
    advisory_url: str | None = None
    risk_level: str = "unknown"


class VulnerabilitiesEntry(BaseModel):
    """Stored per analysis run in the `vulnerabilities` collection."""

    records: list[VulnerabilityRecord] = Field(default_factory=list)
    total_findings: int = 0
    concern: str = ""
