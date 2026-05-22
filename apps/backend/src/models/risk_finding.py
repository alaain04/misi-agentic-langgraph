from __future__ import annotations

from dataclasses import dataclass, field

from src.models.evidence import Severity
from src.models.hypothesis import Hypothesis


@dataclass
class ContradictionReport:
    evidence_ids: list[str]
    description: str
    resolution: str  # "effective_risk_reduced" | "unresolved" | "context_dependent"
    adjusted_confidence: float


@dataclass
class RiskFinding:
    dep_name: str
    risk_score: float  # 0–10
    confidence: float  # 0–1
    severity: Severity
    hypotheses: list[Hypothesis]
    supporting_evidence: list[str]
    contradictions: list[ContradictionReport]
    missing_evidence: list[str]
    summary: str
    recommendation: str | None = None
    alternatives: list[str] = field(default_factory=list)
