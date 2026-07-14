from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from src.models.conductor import FindingNote, ToolCall


class PrepResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    repo_path: str
    project_metadata: dict
    manifest_files: list[str]
    detected_package_manager: str
    dependency_graph: dict
    discovery_summary: str
    vector_store_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class AgentDispatch(BaseModel):
    domain: str
    hypothesis: str
    packages_to_focus: list[str]
    agent_type: str


class AnalysisConductorDecision(BaseModel):
    dispatches: list[AgentDispatch]
    finalize: bool = False
    reasoning: str


class DomainAgentDecision(BaseModel):
    tool_calls: list[ToolCall]
    findings: list[FindingNote]
    summary: str
    confidence: float
    finalize: bool
    reasoning: str


class EvidenceBundle(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    domain: str
    hypothesis: str
    packages_to_focus: list[str] = Field(default_factory=list)
    findings: list[FindingNote]
    summary: str
    confidence: float
    verification_note: str | None = None


class AgentCallRecord(BaseModel):
    conductor_iteration: int
    agent_type: str
    domain: str
    tools_used: list[str]
    react_iterations: int
    started_at: str
    finished_at: str
    bundle_id: str


class AnalysisResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    concern: str
    findings: list[FindingNote]
    evidence_bundle_ids: list[str]
    iteration_count: int
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ReportFinding(BaseModel):
    dep_name: str
    severity: str
    description: str
    recommendation: str
    alternatives: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    evidence: list = Field(default_factory=list)


class ReportConductorDecision(BaseModel):
    tool_calls: list[ToolCall]
    finalize: bool = False
    reasoning: str


class ReportResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    concern: str
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    executive_summary: str
    overall_risk_level: str
    findings: list[ReportFinding]
    recommendations: list[str]
