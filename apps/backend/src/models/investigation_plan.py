from __future__ import annotations
from dataclasses import dataclass, field
from src.models.hypothesis import Hypothesis


@dataclass
class SkillAssignment:
    dep_name: str
    hypothesis_id: str
    skill_id: str


@dataclass
class InvestigationPlan:
    concern: str
    hypotheses: list[Hypothesis]
    skill_plan: list[SkillAssignment]
    rationale: str
    dep_filter: list[str] | None = None
