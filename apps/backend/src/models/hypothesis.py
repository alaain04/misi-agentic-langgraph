from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

HypothesisStatus = Literal["open", "supported", "refuted", "inconclusive"]


@dataclass
class Hypothesis:
    id: str
    dep_name: str
    statement: str
    risk_theme: str
    rationale: str
    skills: list[str]
    status: HypothesisStatus = "open"
    confidence: float | None = None
