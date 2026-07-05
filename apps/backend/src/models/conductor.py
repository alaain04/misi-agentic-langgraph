from pydantic import BaseModel


class ToolCall(BaseModel):
    tool: str
    args: dict
    reason: str


class FindingNote(BaseModel):
    dep_name: str
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    description: str
    evidence_refs: list[str]


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
