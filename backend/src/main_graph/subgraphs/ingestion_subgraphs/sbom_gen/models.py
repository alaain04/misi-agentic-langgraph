from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TrivyVulnerability(BaseModel):
    vulnerability_id: str
    pkg_name: str
    installed_version: str
    fixed_version: str | None = None
    severity: str = "UNKNOWN"
    description: str | None = None
    primary_url: str | None = None


class TrivyLicenseFinding(BaseModel):
    pkg_name: str
    license_name: str
    category: str = "unknown"
    severity: str = "UNKNOWN"
    confidence: float | None = None


class SbomGenEntry(BaseModel):
    """Stored per analysis run in the `sbom_gens` collection."""

    repo_url: str = ""
    vulnerabilities: list[TrivyVulnerability] = Field(default_factory=list)
    licenses: list[TrivyLicenseFinding] = Field(default_factory=list)
    sbom_cyclonedx: dict[str, Any] = Field(default_factory=dict)
    raw_scan: dict[str, Any] = Field(default_factory=dict)
    scan_error: str | None = None
