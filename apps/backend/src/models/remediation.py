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


class FindingSummary(BaseModel):
    dep_name: str
    severity: str
    description: str


class Remediation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    addresses: list[str]  # analysis finding dep_names this covers
    finding_summaries: list[FindingSummary] = Field(default_factory=list)
    target_dep: str  # the DIRECT dep acted on (the anchor)
    required_by: list[str] = Field(default_factory=list)
    strategy: Literal["bump", "bump_with_codemod", "replace"] = "bump"
    from_range: str | None = None
    to_range: str | None = None
    replacement_dep: str | None = None
    replacement_range: str | None = None
    migration_plan: str = ""
    plan: MigrationPlan | None = None  # persisted, reviewable (spec D5)
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
    finding_summaries: list[FindingSummary] = Field(default_factory=list)
    current_range: str | None = None  # from package.json, if known
    # The registry's `latest` dist-tag, resolved once by classify and reused
    # by investigate as the upgrade ceiling. None = could not be resolved.
    latest_version: str | None = None
    # classify's tier verdict. Binding, NOT advisory: r3 means no
    # same-package upgrade fixes this dependency, and routing enforces it.
    tier: Literal["r1", "r2", "r3"] | None = None


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


class ReleaseDigest(BaseModel):
    """Release investigator output for one target."""

    from_version: str | None
    to_version: str | None
    migration_needed: bool  # False => clean bump, no code change
    migration_guide: str = ""  # LLM prose; "" when not needed
    breaking_changes: list[str] = Field(default_factory=list)


class TargetInvestigation(BaseModel):
    """Everything the Migration Planner reads about one target."""

    target_dep: str
    dependents: list[str] = Field(default_factory=list)
    call_sites: list[str] = Field(default_factory=list)
    release: ReleaseDigest


class MigrationTask(BaseModel):
    kind: Literal["bump", "codemod", "replace"]
    rationale: str
    to_range: str | None = None
    files: list[str] = Field(default_factory=list)
    replacement_dep: str | None = None
    replacement_range: str | None = None


class MigrationPlan(BaseModel):
    target_dep: str
    tier_hint: Literal["r1", "r2", "r3"]
    migration_guide: str = ""
    tasks: list[MigrationTask] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)


class MigrationPlanBatch(BaseModel):
    """Structured-output shape for build_migration_plan_node's single
    batched call: one MigrationPlan per target, produced together so the
    model can reason about cross-target `requires` coupling in one pass."""

    plans: list[MigrationPlan] = Field(default_factory=list)
