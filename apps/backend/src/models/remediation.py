from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    installed: bool = False
    built: bool | None = None  # None = repo has no build script
    tested: bool | None = None  # None = repo has no test script
    finding_resolved: bool | None = None  # checkable (vuln re-audit)
    logs_snippet: str = ""


class CodeChange(BaseModel):  # Tier 2/3 slot — empty in v1
    file: str
    rationale: str


class Remediation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    addresses: list[str]  # analysis finding dep_names this covers
    target_dep: str  # the DIRECT dep acted on (the anchor)
    required_by: list[str] = Field(default_factory=list)
    strategy: Literal["bump", "bump_with_codemod", "replace"] = "bump"
    from_range: str | None = None
    to_range: str | None = None
    replacement_dep: str | None = None
    replacement_range: str | None = None
    migration_plan: str = ""
    code_changes: list[CodeChange] = Field(default_factory=list)
    status: Literal["fixed", "failed", "skipped"] = "skipped"
    skip_reason: str | None = None
    verification: VerificationResult = Field(default_factory=VerificationResult)
    attempts: int = 0
    patch: str = ""
    branch: str | None = None
    pr_url: str | None = None


class RemediationResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    remediations: list[Remediation] = Field(default_factory=list)
    consent: bool = False


class RemediationTarget(BaseModel):
    """Internal: a deduped unit of work produced by target selection."""

    target_dep: str  # direct dep to bump
    addresses: list[str]  # finding dep_names grouped under it
    current_range: str | None = None  # from package.json, if known


class RemediationOutcome(BaseModel):
    """Structured final answer of one per-target remediation subagent
    (deepagents `response_format`). `status` here is the agent's OWN
    self-report and is provisional — group_and_verify_gate re-verifies
    independently and is the only thing that sets the Remediation record
    that actually ships. `code_diff` is a unified diff of any file edits
    the agent made (Tier 2/3 only; empty for a plain bump)."""

    strategy: Literal["bump", "bump_with_codemod", "replace"] = "bump"
    to_range: str | None = None
    replacement_dep: str | None = None
    replacement_range: str | None = None
    migration_plan: str = ""
    code_diff: str = ""
    requires: list[str] = Field(default_factory=list)
    status: Literal["fixed", "failed", "skipped"] = "skipped"
    skip_reason: str | None = None
    summary: str = ""
