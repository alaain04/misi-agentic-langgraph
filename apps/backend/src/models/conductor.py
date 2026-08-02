from pydantic import BaseModel


class ToolCall(BaseModel):
    tool: str
    args: dict
    reason: str


class EvidenceRef(BaseModel):
    tool: str
    url: str | None
    log_snippet: str


class FindingNote(BaseModel):
    dep_name: str
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    description: str
    evidence: list[EvidenceRef]
    installed_version: str | None = None
    fixed_version: str | None = None
    # Same-dependency-upgrade comparison only (Trivy always reports Installed/
    # FixedVersion for the same PkgName) - never a signal for a package
    # replacement/migration. None means "not computable": no fix available,
    # or either version string isn't parseable as semver.
    is_semver_major: bool | None = None


class ToolResult(BaseModel):
    id: str
    tool: str
    args: dict
    output: dict
    error: str | None
    duration_ms: int


class ConductorDecision(BaseModel):
    tool_calls: list[ToolCall]
    findings: list[FindingNote]
    ask_user: str | None
    checkpoint_message: str | None
    finalize: bool
    reasoning: str
