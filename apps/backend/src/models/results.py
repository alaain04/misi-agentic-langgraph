from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.models.conductor import FindingNote, ToolCall


class PrepResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    repo_path: str
    repo_url: str = ""
    commit_sha: str = ""
    project_metadata: dict
    manifest_files: list[str]
    package_manager: str
    docker_image: str = "node:lts-alpine"
    dependency_graph: dict
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
    packages_to_focus: list[str] = Field(default_factory=list)
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


class BlastRadiusSummary(BaseModel):
    available: bool
    affected_file_count: int = 0
    affected_files: list[str] = Field(default_factory=list)
    node_count: int = 0
    use_cases_impacted: list[str] = Field(default_factory=list)
    narrative: str = ""
    source: Literal["codegraph", "local_scan", "unavailable"] = "unavailable"


class ReportFinding(BaseModel):
    dep_name: str
    severity: str
    description: str
    recommendation: str
    alternatives: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    evidence: list = Field(default_factory=list)
    business_impact: str = ""
    blast_radius: BlastRadiusSummary | None = None
    trust: bool = True
    observation: str = ""
    # Directness attribution: dep_name is always the package where the issue
    # physically is; is_direct/direct_dependents record whether it is a declared
    # direct dependency and, if transitive, which direct deps pull it in. The
    # recommendation is always framed around the direct dependent(s).
    is_direct: bool = True
    direct_dependents: list[str] = Field(default_factory=list)


class FindingEnrichmentDecision(BaseModel):
    # Field order matters for function-calling structured output: pydantic
    # emits schema properties in declaration order, and the model fills
    # them in that order too. Required scalar fields placed after a large
    # nested object (`finding`, a full ReportFinding) were being dropped
    # from the model's function-call arguments once it started generating
    # that object -- putting reasoning/finalize/tool_calls first, before
    # the large payload, fixed observed missing-field validation errors.
    reasoning: str
    finalize: bool
    tool_calls: list[ToolCall]
    finding: ReportFinding | None


class ImpactAnalysisDecision(BaseModel):
    reasoning: str
    finalize: bool
    tool_calls: list[ToolCall]
    narrative: str
    use_cases_impacted: list[str]


class ReportResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    concern: str
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    executive_summary: str
    overall_risk_level: str
    findings: list[ReportFinding]
    recommendations: list[str]
