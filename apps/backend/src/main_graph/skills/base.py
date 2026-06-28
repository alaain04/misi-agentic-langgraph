from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.models.evidence import Evidence, EvidenceKind


@dataclass
class SkillContext:
    dep_name: str
    hypothesis_id: str
    hypothesis: str
    sbom: dict
    concern: str
    repo_path: str | None = None
    services: dict = field(default_factory=dict)


class InvestigationSkill(ABC):
    id: str
    name: str
    description: str
    trigger_conditions: list[str]
    required_inputs: list[str]
    evidence_kinds: list[EvidenceKind]

    @abstractmethod
    async def execute(self, ctx: SkillContext) -> list[Evidence]: ...

    def can_run(self, ctx: SkillContext) -> bool:
        return all(getattr(ctx, f, None) is not None for f in self.required_inputs)
