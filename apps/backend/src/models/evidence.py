# src/models/evidence.py
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

Severity = Literal["critical", "high", "medium", "low", "info"]
EvidenceKind = Literal[
    "vulnerability",
    "maintainer_signal",
    "supply_chain_signal",
    "license_signal",
    "reachability_signal",
    "blast_radius_signal",
    "release_anomaly",
    "ecosystem_signal",
]


@dataclass
class Evidence:
    kind: EvidenceKind
    dep_name: str
    skill_id: str
    hypothesis_id: str
    signal: str
    raw_data: dict
    source: str
    reliability: float
    confidence: float
    supports_hypothesis: bool
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    collected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    source_url: str | None = None
    severity: Severity | None = None
    contradicts_evidence: list[str] = field(default_factory=list)
