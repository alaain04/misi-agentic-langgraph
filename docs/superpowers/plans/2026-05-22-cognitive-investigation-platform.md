# Cognitive Investigation Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the dependency risk pipeline from subgraph-selection into a hypothesis-driven, skill-based investigation platform with typed evidence, deterministic confidence scoring, contradiction detection, and dual HITL gates.

**Architecture:** Eight stable backbone nodes replace the current variable pipeline. An `investigation_planner` generates falsifiable hypotheses and skill assignments per dependency. A `skill_executor` fan-out (via `Send()`) produces typed `Evidence[]` into shared state. An `evidence_correlator` applies rule-based contradiction detection and confidence arithmetic before LLM synthesis.

**Tech Stack:** Python 3.12, LangGraph ≥1.0, LangChain ≥1.0, pytest + pytest-asyncio (asyncio_mode=auto), uv

**Phase 4 (empirical calibration + thesis validation) is deferred** — plan it after Phase 3 is complete and running against real CodeTech projects.

**Working directory for all commands:** `apps/backend/`

---

## Phase 1 — Foundation

### Task 1: Evidence model

**Files:**
- Create: `src/models/evidence.py`
- Create: `tests/unit/models/__init__.py`
- Create: `tests/unit/models/test_evidence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/models/test_evidence.py
from src.models.evidence import Evidence, EvidenceKind, Severity


def test_evidence_auto_fields():
    ev = Evidence(
        kind="vulnerability",
        dep_name="lodash",
        skill_id="VulnerabilitySkill",
        hypothesis_id="h1",
        signal="CVE-2021-23337 in lodash@4.17.20",
        raw_data={"cve_id": "CVE-2021-23337"},
        source="trivy",
        reliability=0.95,
        confidence=0.9,
        supports_hypothesis=True,
    )
    assert len(ev.id) == 32          # uuid4().hex
    assert "T" in ev.collected_at   # ISO timestamp
    assert ev.contradicts_evidence == []
    assert ev.severity is None
    assert ev.source_url is None


def test_evidence_with_severity():
    ev = Evidence(
        kind="vulnerability",
        dep_name="lodash",
        skill_id="VulnerabilitySkill",
        hypothesis_id="h1",
        signal="critical vuln",
        raw_data={},
        source="trivy",
        reliability=0.95,
        confidence=0.9,
        supports_hypothesis=True,
        severity="critical",
    )
    assert ev.severity == "critical"


def test_evidence_contradicts():
    ev = Evidence(
        kind="reachability_signal",
        dep_name="lodash",
        skill_id="ReachabilitySkill",
        hypothesis_id="h1",
        signal="not imported",
        raw_data={},
        source="ast_scan",
        reliability=0.8,
        confidence=0.82,
        supports_hypothesis=False,
        contradicts_evidence=["ev_abc"],
    )
    assert ev.contradicts_evidence == ["ev_abc"]
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/models/test_evidence.py -v
```
Expected: `ModuleNotFoundError: No module named 'src.models.evidence'`

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/models/test_evidence.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/models/evidence.py tests/unit/models/
git commit -m "feat: add Evidence model"
```

---

### Task 2: Hypothesis and InvestigationPlan models

**Files:**
- Create: `src/models/hypothesis.py`
- Create: `src/models/investigation_plan.py`
- Create: `tests/unit/models/test_investigation_plan.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/models/test_investigation_plan.py
from src.models.hypothesis import Hypothesis
from src.models.investigation_plan import InvestigationPlan, SkillAssignment


def test_hypothesis_defaults():
    h = Hypothesis(
        id="h1",
        dep_name="lodash",
        statement="lodash may expose prototype pollution",
        risk_theme="vulnerability",
        rationale="lodash has known CVEs",
        skills=["VulnerabilitySkill"],
    )
    assert h.status == "open"
    assert h.confidence is None


def test_investigation_plan():
    plan = InvestigationPlan(
        concern="security audit",
        hypotheses=[
            Hypothesis(
                id="h1",
                dep_name="lodash",
                statement="lodash may expose prototype pollution",
                risk_theme="vulnerability",
                rationale="known CVEs",
                skills=["VulnerabilitySkill"],
            )
        ],
        skill_plan=[
            SkillAssignment(dep_name="lodash", hypothesis_id="h1", skill_id="VulnerabilitySkill")
        ],
        rationale="security focus given concern",
    )
    assert len(plan.hypotheses) == 1
    assert len(plan.skill_plan) == 1
    assert plan.dep_filter is None
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/models/test_investigation_plan.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/models/hypothesis.py
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
```

```python
# src/models/investigation_plan.py
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
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/models/test_investigation_plan.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/models/hypothesis.py src/models/investigation_plan.py tests/unit/models/test_investigation_plan.py
git commit -m "feat: add Hypothesis and InvestigationPlan models"
```

---

### Task 3: RiskFinding and ContradictionReport models

**Files:**
- Create: `src/models/risk_finding.py`
- Create: `tests/unit/models/test_risk_finding.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/models/test_risk_finding.py
from src.models.risk_finding import ContradictionReport, RiskFinding


def test_contradiction_report():
    c = ContradictionReport(
        evidence_ids=["ev1", "ev2"],
        description="high CVE but dep is unreachable",
        resolution="effective_risk_reduced",
        adjusted_confidence=0.35,
    )
    assert c.adjusted_confidence == 0.35


def test_risk_finding_defaults():
    from src.models.hypothesis import Hypothesis
    h = Hypothesis(id="h1", dep_name="lodash", statement="s", risk_theme="vulnerability", rationale="r", skills=[])
    f = RiskFinding(
        dep_name="lodash",
        risk_score=7.2,
        confidence=0.8,
        severity="high",
        hypotheses=[h],
        supporting_evidence=["ev1"],
        contradictions=[],
        missing_evidence=[],
        summary="lodash has a high-severity CVE with strong evidence",
    )
    assert f.recommendation is None
    assert f.alternatives == []
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/models/test_risk_finding.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/models/risk_finding.py
from __future__ import annotations
from dataclasses import dataclass, field
from src.models.evidence import Severity
from src.models.hypothesis import Hypothesis


@dataclass
class ContradictionReport:
    evidence_ids: list[str]
    description: str
    resolution: str   # "effective_risk_reduced" | "unresolved" | "context_dependent"
    adjusted_confidence: float


@dataclass
class RiskFinding:
    dep_name: str
    risk_score: float           # 0–10
    confidence: float           # 0–1
    severity: Severity
    hypotheses: list[Hypothesis]
    supporting_evidence: list[str]
    contradictions: list[ContradictionReport]
    missing_evidence: list[str]
    summary: str
    recommendation: str | None = None
    alternatives: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/models/test_risk_finding.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/models/risk_finding.py tests/unit/models/test_risk_finding.py
git commit -m "feat: add RiskFinding and ContradictionReport models"
```

---

### Task 4: Confidence arithmetic utilities

**Files:**
- Create: `src/main_graph/utils/confidence.py`
- Create: `tests/unit/utils/test_confidence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/utils/test_confidence.py
from src.models.evidence import Evidence
from src.models.risk_finding import ContradictionReport
from src.main_graph.utils.confidence import (
    compute_confidence,
    compute_risk_score,
    compute_severity,
)


def _make_evidence(kind, dep, skill, confidence, reliability, supports, severity=None):
    return Evidence(
        kind=kind, dep_name=dep, skill_id=skill, hypothesis_id="h1",
        signal="signal", raw_data={}, source="test",
        reliability=reliability, confidence=confidence,
        supports_hypothesis=supports, severity=severity,
    )


def test_compute_confidence_basic():
    evs = [
        _make_evidence("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True),
        _make_evidence("maintainer_signal", "lodash", "MaintainerTrustSkill", 0.7, 0.8, True),
    ]
    score = compute_confidence(evs, [], "lodash")
    # base = (0.9*0.95 + 0.7*0.8) / 2 = (0.855 + 0.56) / 2 = 0.7075
    # bonus: 2 different skills supporting → +0.1
    assert 0.79 < score <= 0.85


def test_compute_confidence_empty():
    assert compute_confidence([], [], "lodash") == 0.0


def test_compute_confidence_unresolved_contradiction_penalty():
    evs = [_make_evidence("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True)]
    contradictions = [ContradictionReport(
        evidence_ids=[evs[0].id],
        description="test",
        resolution="unresolved",
        adjusted_confidence=0.3,
    )]
    score = compute_confidence(evs, contradictions, "lodash")
    # base ≈ 0.855, penalty = 0.2 → 0.655
    assert score < 0.76


def test_compute_severity_critical_wins():
    evs = [
        _make_evidence("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True, "critical"),
        _make_evidence("ecosystem_signal", "lodash", "EcosystemSkill", 0.5, 0.7, True, "low"),
    ]
    assert compute_severity(evs) == "critical"


def test_compute_severity_no_supporting():
    evs = [_make_evidence("reachability_signal", "lodash", "ReachabilitySkill", 0.8, 0.9, False)]
    assert compute_severity(evs) == "low"


def test_compute_risk_score():
    assert compute_risk_score("critical", 1.0) == 10.0
    assert compute_risk_score("high", 0.5) == 3.8
    assert compute_risk_score("medium", 0.8) == 4.0
    assert compute_risk_score("low", 1.0) == 2.5
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/utils/test_confidence.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/main_graph/utils/confidence.py
from __future__ import annotations

from src.models.evidence import Evidence, Severity
from src.models.risk_finding import ContradictionReport

_SEVERITY_ORDER: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_SEVERITY_BASE: dict[str, float] = {"critical": 10.0, "high": 7.5, "medium": 5.0, "low": 2.5}


def compute_confidence(
    evidence: list[Evidence],
    contradictions: list[ContradictionReport],
    dep_name: str,
) -> float:
    dep_evs = [e for e in evidence if e.dep_name == dep_name]
    if not dep_evs:
        return 0.0

    base = sum(e.confidence * e.reliability for e in dep_evs) / len(dep_evs)

    dep_ev_ids = {e.id for e in dep_evs}
    unresolved = sum(
        1 for c in contradictions
        if c.resolution == "unresolved" and any(eid in dep_ev_ids for eid in c.evidence_ids)
    )
    penalty = 0.2 * unresolved

    supporting_skills = {e.skill_id for e in dep_evs if e.supports_hypothesis}
    bonus = 0.1 if len(supporting_skills) >= 2 else 0.0

    return max(0.0, min(1.0, base - penalty + bonus))


def compute_severity(evidence: list[Evidence]) -> Severity:
    supporting = [e for e in evidence if e.supports_hypothesis and e.severity]
    if not supporting:
        return "low"
    best = max(supporting, key=lambda e: _SEVERITY_ORDER.get(e.severity or "info", 0))
    return best.severity or "low"


def compute_risk_score(severity: Severity, confidence: float) -> float:
    return round(_SEVERITY_BASE.get(severity, 2.5) * confidence, 1)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/utils/test_confidence.py -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/utils/confidence.py tests/unit/utils/test_confidence.py
git commit -m "feat: add confidence arithmetic utilities"
```

---

### Task 5: InvestigationSkill ABC, SkillContext, and registry stubs

**Files:**
- Create: `src/main_graph/skills/__init__.py`
- Create: `src/main_graph/skills/base.py`
- Create: `src/main_graph/skills/registry.py`
- Create: `src/main_graph/skills/vulnerability.py`
- Create: `src/main_graph/skills/license.py`
- Create: `src/main_graph/skills/reachability.py`
- Create: `src/main_graph/skills/maintainer_trust.py`
- Create: `src/main_graph/skills/release_anomaly.py`
- Create: `src/main_graph/skills/supply_chain.py`
- Create: `src/main_graph/skills/ecosystem.py`
- Create: `src/main_graph/skills/blast_radius.py`
- Create: `tests/unit/skills/__init__.py`
- Create: `tests/unit/skills/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/skills/test_registry.py
from src.main_graph.skills.registry import SKILL_REGISTRY, SKILL_DESCRIPTIONS

EXPECTED_SKILL_IDS = {
    "VulnerabilitySkill",
    "MaintainerTrustSkill",
    "SupplyChainSkill",
    "LicenseSkill",
    "ReachabilitySkill",
    "BlastRadiusSkill",
    "ReleaseAnomalySkill",
    "EcosystemSkill",
}


def test_all_skills_registered():
    assert set(SKILL_REGISTRY.keys()) == EXPECTED_SKILL_IDS


def test_skill_descriptions_match_registry():
    assert set(SKILL_DESCRIPTIONS.keys()) == EXPECTED_SKILL_IDS


def test_each_skill_has_required_attributes():
    for skill_id, skill in SKILL_REGISTRY.items():
        assert skill.id == skill_id
        assert skill.name
        assert skill.description
        assert skill.trigger_conditions
        assert skill.required_inputs is not None
        assert skill.evidence_kinds


async def test_stub_execute_returns_empty_list():
    from src.main_graph.skills.base import SkillContext
    ctx = SkillContext(
        dep_name="lodash",
        hypothesis_id="h1",
        hypothesis="lodash may be risky",
        sbom={},
        concern="security",
    )
    for skill in SKILL_REGISTRY.values():
        result = await skill.execute(ctx)
        assert isinstance(result, list)
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/skills/test_registry.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement base**

```python
# src/main_graph/skills/base.py
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
```

- [ ] **Step 4: Create each skill stub** — one file each. All stubs follow the same pattern:

```python
# src/main_graph/skills/vulnerability.py
from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class VulnerabilitySkill(InvestigationSkill):
    id = "VulnerabilitySkill"
    name = "Vulnerability Surface Assessment"
    description = "Scans for known CVEs and security advisories via Trivy"
    trigger_conditions = ["security", "CVE", "vulnerability", "supply chain"]
    required_inputs = ["repo_path"]
    evidence_kinds = ["vulnerability"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
```

```python
# src/main_graph/skills/license.py
from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class LicenseSkill(InvestigationSkill):
    id = "LicenseSkill"
    name = "License Compliance Assessment"
    description = "Checks license compatibility and copyleft obligations"
    trigger_conditions = ["license", "commercial use", "copyleft", "compliance"]
    required_inputs = ["repo_path"]
    evidence_kinds = ["license_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
```

```python
# src/main_graph/skills/reachability.py
from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class ReachabilitySkill(InvestigationSkill):
    id = "ReachabilitySkill"
    name = "Reachability Assessment"
    description = "Determines if a dependency is actually imported and used in execution paths"
    trigger_conditions = ["code impact", "unused", "tree shaking", "reachability"]
    required_inputs = ["repo_path", "dep_name"]
    evidence_kinds = ["reachability_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
```

```python
# src/main_graph/skills/maintainer_trust.py
from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class MaintainerTrustSkill(InvestigationSkill):
    id = "MaintainerTrustSkill"
    name = "Maintainer Trust Analysis"
    description = "Evaluates maintainer activity, commit patterns, and issue responsiveness"
    trigger_conditions = ["abandoned", "maintainer", "activity", "bus factor"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["maintainer_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
```

```python
# src/main_graph/skills/release_anomaly.py
from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class ReleaseAnomalySkill(InvestigationSkill):
    id = "ReleaseAnomalySkill"
    name = "Release Anomaly Detection"
    description = "Detects suspicious release patterns: rapid publishing, version gaps, ownership changes"
    trigger_conditions = ["release", "version", "publish", "anomaly", "typosquat"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["release_anomaly"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
```

```python
# src/main_graph/skills/supply_chain.py
from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class SupplyChainSkill(InvestigationSkill):
    id = "SupplyChainSkill"
    name = "Supply Chain Integrity Assessment"
    description = "Checks provenance, install scripts, typosquatting indicators, and registry metadata"
    trigger_conditions = ["supply chain", "provenance", "typosquat", "install script", "compromise"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["supply_chain_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
```

```python
# src/main_graph/skills/ecosystem.py
from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class EcosystemSkill(InvestigationSkill):
    id = "EcosystemSkill"
    name = "Ecosystem Reputation Analysis"
    description = "Assesses npm download trends, community health, and package popularity signals"
    trigger_conditions = ["popularity", "downloads", "community", "ecosystem", "reputation"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["ecosystem_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
```

```python
# src/main_graph/skills/blast_radius.py
from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class BlastRadiusSkill(InvestigationSkill):
    id = "BlastRadiusSkill"
    name = "Blast Radius Estimation"
    description = "Computes transitive dependents and graph depth to estimate change impact"
    trigger_conditions = ["blast radius", "transitive", "impact", "fanout", "graph"]
    required_inputs = ["dep_name", "sbom"]
    evidence_kinds = ["blast_radius_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
```

- [ ] **Step 5: Create registry**

```python
# src/main_graph/skills/registry.py
from src.main_graph.skills.blast_radius import BlastRadiusSkill
from src.main_graph.skills.ecosystem import EcosystemSkill
from src.main_graph.skills.license import LicenseSkill
from src.main_graph.skills.maintainer_trust import MaintainerTrustSkill
from src.main_graph.skills.reachability import ReachabilitySkill
from src.main_graph.skills.release_anomaly import ReleaseAnomalySkill
from src.main_graph.skills.supply_chain import SupplyChainSkill
from src.main_graph.skills.vulnerability import VulnerabilitySkill
from src.main_graph.skills.base import InvestigationSkill

SKILL_REGISTRY: dict[str, InvestigationSkill] = {
    skill.id: skill
    for skill in [
        VulnerabilitySkill(),
        MaintainerTrustSkill(),
        SupplyChainSkill(),
        LicenseSkill(),
        ReachabilitySkill(),
        BlastRadiusSkill(),
        ReleaseAnomalySkill(),
        EcosystemSkill(),
    ]
}

SKILL_DESCRIPTIONS: dict[str, str] = {
    sid: f"{s.name}: {s.description} | triggers: {', '.join(s.trigger_conditions)}"
    for sid, s in SKILL_REGISTRY.items()
}
```

```python
# src/main_graph/skills/__init__.py
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/skills/test_registry.py -v
```
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add src/main_graph/skills/ tests/unit/skills/
git commit -m "feat: add InvestigationSkill ABC, SkillContext, and skill registry stubs"
```

---

### Task 6: Update MainState

**Files:**
- Modify: `src/main_graph/state.py`
- Create: `tests/unit/models/test_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/models/test_state.py
import operator
from typing import Annotated, get_type_hints

from src.main_graph.state import MainState


def test_state_has_evidence_field():
    hints = get_type_hints(MainState, include_extras=True)
    assert "evidence" in hints


def test_state_has_investigation_plan_field():
    hints = get_type_hints(MainState, include_extras=True)
    assert "investigation_plan" in hints


def test_state_has_risk_findings_field():
    hints = get_type_hints(MainState, include_extras=True)
    assert "risk_findings" in hints
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/models/test_state.py -v
```
Expected: AssertionError (fields missing)

- [ ] **Step 3: Update `src/main_graph/state.py`**

Replace the entire file:

```python
# backend/src/main_graph/state.py
import operator
from typing import Annotated, Any, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import ProjectMetadata
from src.models.evidence import Evidence
from src.models.investigation_plan import InvestigationPlan
from src.models.risk_finding import ContradictionReport, RiskFinding


class MainState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────────
    repo_url: str
    concern: str
    job_id: str

    # ── Discovery (unchanged) ────────────────────────────────────────────────
    repo_path: NotRequired[str]
    project_metadata: NotRequired[ProjectMetadata]
    manifest_files: NotRequired[list[str]]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
    sbom_cyclonedx: NotRequired[dict[str, Any]]
    sbom_result_id: NotRequired[str]
    sbom_error: NotRequired[str | None]

    # ── Investigation plan ───────────────────────────────────────────────────
    investigation_plan: NotRequired[InvestigationPlan]
    messages: Annotated[list, add_messages]

    # ── Evidence (fan-in reducer) ────────────────────────────────────────────
    evidence: Annotated[list[Evidence], operator.add]

    # ── Skill execution (Send() fields) ─────────────────────────────────────
    current_skill_id: NotRequired[str]
    current_dep_name: NotRequired[str]
    current_hypothesis_id: NotRequired[str]

    # ── Correlation outputs ──────────────────────────────────────────────────
    risk_findings: NotRequired[list[RiskFinding]]
    contradictions: NotRequired[list[ContradictionReport]]
    reviewer_feedback: NotRequired[str]
    review_approved: NotRequired[bool]
    review_iterations: NotRequired[int]
    analysis_report: NotRequired[dict[str, Any]]

    # ── Control ──────────────────────────────────────────────────────────────
    cancelled: NotRequired[bool]
```

- [ ] **Step 4: Run all unit tests to verify nothing breaks**

```bash
uv run pytest tests/unit/ -v
```
Expected: all existing tests still pass + 3 new state tests pass

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/state.py tests/unit/models/test_state.py
git commit -m "feat: update MainState with evidence, investigation_plan, and risk_findings fields"
```

---

## Phase 2 — Skills

### Task 7: LicenseSkill (real implementation)

**Files:**
- Modify: `src/main_graph/skills/license.py`
- Create: `tests/unit/skills/test_license_skill.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/skills/test_license_skill.py
import json
from unittest.mock import AsyncMock

import pytest

from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.license import LicenseSkill
from src.domain.ports.container_run_port import ContainerRunPort


def _make_ctx(repo_path="/tmp/repo"):
    return SkillContext(
        dep_name="lodash",
        hypothesis_id="h1",
        hypothesis="lodash may have license violations",
        sbom={},
        concern="license compliance",
        repo_path=repo_path,
        services={"container": AsyncMock(spec=ContainerRunPort)},
    )


async def test_license_skill_produces_evidence():
    trivy_output = {
        "Results": [{"Licenses": [
            {"PkgName": "lodash", "Name": "GPL-3.0", "Category": "restricted"},
        ]}]
    }
    ctx = _make_ctx()
    ctx.services["container"].run.return_value = (0, json.dumps(trivy_output), "")

    skill = LicenseSkill()
    evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "license_signal"
    assert ev.dep_name == "lodash"
    assert ev.skill_id == "LicenseSkill"
    assert ev.severity in ("high", "medium", "low")
    assert ev.supports_hypothesis is True
    assert 0.0 <= ev.confidence <= 1.0


async def test_license_skill_no_repo_path_returns_empty():
    ctx = _make_ctx(repo_path=None)
    skill = LicenseSkill()
    evidence = await skill.execute(ctx)
    assert evidence == []


async def test_license_skill_permissive_license_low_severity():
    trivy_output = {
        "Results": [{"Licenses": [
            {"PkgName": "express", "Name": "MIT", "Category": "permissive"},
        ]}]
    }
    ctx = _make_ctx()
    ctx.dep_name = "express"
    ctx.services["container"].run.return_value = (0, json.dumps(trivy_output), "")

    skill = LicenseSkill()
    evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].severity == "low"
    assert evidence[0].supports_hypothesis is False
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/skills/test_license_skill.py -v
```
Expected: all fail (execute returns `[]`)

- [ ] **Step 3: Implement**

```python
# src/main_graph/skills/license.py
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)

_RISKY_LICENSES = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.0", "LGPL-2.1"}
_RISKY_CATEGORIES = {"restricted", "reciprocal", "unknown"}
_SEVERITY_MAP = {"restricted": "high", "reciprocal": "medium", "unknown": "medium",
                 "permissive": "low", "notice": "low"}


def _license_severity(category: str, license_name: str) -> str:
    if license_name in _RISKY_LICENSES or category.lower() in _RISKY_CATEGORIES:
        return "high" if category.lower() == "restricted" or license_name in _RISKY_LICENSES else "medium"
    return "low"


def _is_violation(category: str, license_name: str) -> bool:
    return category.lower() in _RISKY_CATEGORIES or license_name in _RISKY_LICENSES


class LicenseSkill(InvestigationSkill):
    id = "LicenseSkill"
    name = "License Compliance Assessment"
    description = "Checks license compatibility and copyleft obligations"
    trigger_conditions = ["license", "commercial use", "copyleft", "compliance"]
    required_inputs = ["repo_path"]
    evidence_kinds = ["license_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        if not ctx.repo_path:
            return []

        container = ctx.services.get("container")
        if container is None:
            return []

        try:
            scan_data, _ = await run_trivy(
                container, ctx.repo_path, "--format", "json", "--scanners", "license"
            )
        except Exception:
            logger.exception("LicenseSkill: Trivy scan failed for %s", ctx.dep_name)
            return []

        raw_licenses = [
            lic
            for result in scan_data.get("Results", [])
            for lic in (result.get("Licenses") or [])
            if lic.get("PkgName") == ctx.dep_name
        ]

        evidence = []
        for lic in raw_licenses:
            category = lic.get("Category", "unknown")
            license_name = lic.get("Name", "unknown")
            severity = _license_severity(category, license_name)
            is_violation = _is_violation(category, license_name)
            evidence.append(Evidence(
                id=uuid.uuid4().hex,
                kind="license_signal",
                dep_name=ctx.dep_name,
                skill_id=self.id,
                hypothesis_id=ctx.hypothesis_id,
                collected_at=datetime.now(UTC).isoformat(),
                signal=f"{ctx.dep_name} uses {license_name} (category={category})",
                raw_data=lic,
                source="trivy",
                reliability=0.9,
                confidence=0.85 if is_violation else 0.3,
                severity=severity,
                supports_hypothesis=is_violation,
            ))

        return evidence
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/skills/test_license_skill.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/skills/license.py tests/unit/skills/test_license_skill.py
git commit -m "feat: implement LicenseSkill"
```

---

### Task 8: VulnerabilitySkill (real implementation)

**Files:**
- Modify: `src/main_graph/skills/vulnerability.py`
- Create: `tests/unit/skills/test_vulnerability_skill.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/skills/test_vulnerability_skill.py
import json
from unittest.mock import AsyncMock

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.vulnerability import VulnerabilitySkill


def _make_ctx(repo_path="/tmp/repo"):
    return SkillContext(
        dep_name="lodash",
        hypothesis_id="h1",
        hypothesis="lodash may have CVEs",
        sbom={},
        concern="security",
        repo_path=repo_path,
        services={"container": AsyncMock(spec=ContainerRunPort)},
    )


async def test_vulnerability_skill_produces_evidence():
    trivy_output = {"Results": [{"Vulnerabilities": [{
        "PkgName": "lodash",
        "InstalledVersion": "4.17.15",
        "VulnerabilityID": "CVE-2021-23337",
        "Severity": "HIGH",
        "Description": "Prototype pollution",
        "FixedVersion": "4.17.21",
    }]}]}
    ctx = _make_ctx()
    ctx.services["container"].run.return_value = (0, json.dumps(trivy_output), "")

    skill = VulnerabilitySkill()
    evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "vulnerability"
    assert ev.dep_name == "lodash"
    assert ev.skill_id == "VulnerabilitySkill"
    assert ev.severity == "high"
    assert ev.supports_hypothesis is True
    assert ev.confidence > 0.7
    assert "CVE-2021-23337" in ev.signal


async def test_vulnerability_skill_filters_by_dep():
    trivy_output = {"Results": [{"Vulnerabilities": [
        {"PkgName": "lodash", "InstalledVersion": "4.17.15", "VulnerabilityID": "CVE-A", "Severity": "HIGH"},
        {"PkgName": "express", "InstalledVersion": "4.18.0", "VulnerabilityID": "CVE-B", "Severity": "MEDIUM"},
    ]}]}
    ctx = _make_ctx()
    ctx.services["container"].run.return_value = (0, json.dumps(trivy_output), "")

    skill = VulnerabilitySkill()
    evidence = await skill.execute(ctx)

    assert all(ev.dep_name == "lodash" for ev in evidence)
    assert len(evidence) == 1


async def test_vulnerability_skill_no_repo_path():
    ctx = _make_ctx(repo_path=None)
    skill = VulnerabilitySkill()
    assert await skill.execute(ctx) == []
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/skills/test_vulnerability_skill.py -v
```
Expected: all fail

- [ ] **Step 3: Implement**

```python
# src/main_graph/skills/vulnerability.py
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)

_SEVERITY_CONFIDENCE = {"CRITICAL": 0.95, "HIGH": 0.85, "MEDIUM": 0.65, "LOW": 0.4}
_SEVERITY_NORM = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low"}


class VulnerabilitySkill(InvestigationSkill):
    id = "VulnerabilitySkill"
    name = "Vulnerability Surface Assessment"
    description = "Scans for known CVEs and security advisories via Trivy"
    trigger_conditions = ["security", "CVE", "vulnerability", "supply chain"]
    required_inputs = ["repo_path"]
    evidence_kinds = ["vulnerability"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        if not ctx.repo_path:
            return []

        container = ctx.services.get("container")
        if container is None:
            return []

        try:
            scan_data, _ = await run_trivy(
                container, ctx.repo_path, "--format", "json", "--scanners", "vuln"
            )
        except Exception:
            logger.exception("VulnerabilitySkill: Trivy scan failed for %s", ctx.dep_name)
            return []

        raw_vulns = [
            v
            for result in scan_data.get("Results", [])
            for v in (result.get("Vulnerabilities") or [])
            if v.get("PkgName") == ctx.dep_name
        ]

        return [
            Evidence(
                id=uuid.uuid4().hex,
                kind="vulnerability",
                dep_name=ctx.dep_name,
                skill_id=self.id,
                hypothesis_id=ctx.hypothesis_id,
                collected_at=datetime.now(UTC).isoformat(),
                signal=f"{v.get('VulnerabilityID', 'unknown')} ({v.get('Severity', 'UNKNOWN')}) in {ctx.dep_name}@{v.get('InstalledVersion', '?')}",
                raw_data=v,
                source="trivy",
                reliability=0.95,
                confidence=_SEVERITY_CONFIDENCE.get(v.get("Severity", ""), 0.3),
                severity=_SEVERITY_NORM.get(v.get("Severity", ""), "low"),
                supports_hypothesis=True,
            )
            for v in raw_vulns
        ]
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/skills/test_vulnerability_skill.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/skills/vulnerability.py tests/unit/skills/test_vulnerability_skill.py
git commit -m "feat: implement VulnerabilitySkill"
```

---

### Task 9: ReachabilitySkill (real implementation)

**Files:**
- Modify: `src/main_graph/skills/reachability.py`
- Create: `tests/unit/skills/test_reachability_skill.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/skills/test_reachability_skill.py
from unittest.mock import AsyncMock, patch

from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.reachability import ReachabilitySkill


def _make_ctx(repo_path="/tmp/repo"):
    return SkillContext(
        dep_name="lodash",
        hypothesis_id="h1",
        hypothesis="lodash may be unreachable",
        sbom={},
        concern="impact",
        repo_path=repo_path,
        services={},
    )


async def test_reachability_skill_dep_is_used():
    ctx = _make_ctx()
    skill = ReachabilitySkill()

    with patch("src.main_graph.skills.reachability.find_usages") as mock_find:
        mock_find.return_value = '["src/utils.ts:import lodash"]'
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "reachability_signal"
    assert ev.supports_hypothesis is False  # dep IS reachable → does not support "may be risky due to unreachability"
    assert ev.confidence > 0.5


async def test_reachability_skill_dep_not_used():
    ctx = _make_ctx()
    skill = ReachabilitySkill()

    with patch("src.main_graph.skills.reachability.find_usages") as mock_find:
        mock_find.return_value = "[]"
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "reachability_signal"
    assert ev.supports_hypothesis is True  # dep NOT reachable → supports hypothesis that risk is low
    assert "not found" in ev.signal.lower() or "unreachable" in ev.signal.lower()


async def test_reachability_skill_no_repo_path():
    ctx = _make_ctx(repo_path=None)
    skill = ReachabilitySkill()
    assert await skill.execute(ctx) == []
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/skills/test_reachability_skill.py -v
```
Expected: all fail

- [ ] **Step 3: Implement**

```python
# src/main_graph/skills/reachability.py
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.main_graph.subgraphs.ingestion_subgraphs.impact.tools.filesystem import find_usages
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


class ReachabilitySkill(InvestigationSkill):
    id = "ReachabilitySkill"
    name = "Reachability Assessment"
    description = "Determines if a dependency is actually imported in execution paths"
    trigger_conditions = ["code impact", "unused", "tree shaking", "reachability"]
    required_inputs = ["repo_path", "dep_name"]
    evidence_kinds = ["reachability_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        if not ctx.repo_path or not ctx.dep_name:
            return []

        try:
            raw = find_usages.invoke({"dep_name": ctx.dep_name, "repo_path": ctx.repo_path})
            usages = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            logger.exception("ReachabilitySkill: find_usages failed for %s", ctx.dep_name)
            return []

        is_used = len(usages) > 0
        return [Evidence(
            id=uuid.uuid4().hex,
            kind="reachability_signal",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            collected_at=datetime.now(UTC).isoformat(),
            signal=(
                f"{ctx.dep_name} is imported in {len(usages)} location(s)"
                if is_used
                else f"{ctx.dep_name} not found in any import — dependency appears unreachable"
            ),
            raw_data={"usages": usages},
            source="ast_scan",
            reliability=0.8,
            confidence=0.8 if is_used else 0.75,
            severity="info",
            supports_hypothesis=not is_used,
        )]
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/skills/test_reachability_skill.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/skills/reachability.py tests/unit/skills/test_reachability_skill.py
git commit -m "feat: implement ReachabilitySkill"
```

---

### Task 10: MaintainerTrustSkill and ReleaseAnomalySkill

**Files:**
- Modify: `src/main_graph/skills/maintainer_trust.py`
- Modify: `src/main_graph/skills/release_anomaly.py`
- Create: `tests/unit/skills/test_maintainer_skills.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/skills/test_maintainer_skills.py
from unittest.mock import AsyncMock, patch

from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.maintainer_trust import MaintainerTrustSkill
from src.main_graph.skills.release_anomaly import ReleaseAnomalySkill


def _make_ctx(dep="lodash"):
    return SkillContext(
        dep_name=dep,
        hypothesis_id="h1",
        hypothesis=f"{dep} may be abandoned",
        sbom={},
        concern="maintainer trust",
        services={"mcp_client": AsyncMock()},
    )


async def test_maintainer_trust_active_project():
    ctx = _make_ctx()
    skill = MaintainerTrustSkill()

    with patch("src.main_graph.skills.maintainer_trust._fetch_repo_data") as mock:
        mock.return_value = {
            "commits_last_90_days": 45,
            "open_issues": 12,
            "closed_issues_last_90_days": 30,
            "contributors": 8,
        }
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "maintainer_signal"
    assert ev.supports_hypothesis is False  # active project → not abandoned


async def test_maintainer_trust_abandoned_project():
    ctx = _make_ctx()
    skill = MaintainerTrustSkill()

    with patch("src.main_graph.skills.maintainer_trust._fetch_repo_data") as mock:
        mock.return_value = {
            "commits_last_90_days": 0,
            "open_issues": 150,
            "closed_issues_last_90_days": 0,
            "contributors": 1,
        }
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].supports_hypothesis is True
    assert evidence[0].severity in ("high", "medium")


async def test_release_anomaly_suspicious_pattern():
    ctx = _make_ctx()
    skill = ReleaseAnomalySkill()

    with patch("src.main_graph.skills.release_anomaly._fetch_releases") as mock:
        mock.return_value = [
            {"version": "1.0.0", "days_since_previous": 2},
            {"version": "1.0.1", "days_since_previous": 1},
            {"version": "1.0.2", "days_since_previous": 1},
        ]
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].kind == "release_anomaly"


async def test_release_anomaly_normal_pattern():
    ctx = _make_ctx()
    skill = ReleaseAnomalySkill()

    with patch("src.main_graph.skills.release_anomaly._fetch_releases") as mock:
        mock.return_value = [
            {"version": "1.0.0", "days_since_previous": 90},
            {"version": "2.0.0", "days_since_previous": 180},
        ]
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].supports_hypothesis is False
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/skills/test_maintainer_skills.py -v
```
Expected: all fail

- [ ] **Step 3: Implement MaintainerTrustSkill**

```python
# src/main_graph/skills/maintainer_trust.py
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


async def _fetch_repo_data(dep_name: str, mcp_client) -> dict:
    """Fetch commit and issue metrics from GitHub MCP."""
    try:
        result = await mcp_client.call_tool("get_repository_activity", {"package": dep_name})
        return result or {}
    except Exception:
        logger.warning("MaintainerTrustSkill: MCP fetch failed for %s", dep_name)
        return {}


def _assess_health(data: dict) -> tuple[bool, str, str]:
    """Returns (is_concerning, signal, severity)."""
    commits = data.get("commits_last_90_days", 0)
    open_issues = data.get("open_issues", 0)
    closed = data.get("closed_issues_last_90_days", 0)
    contributors = data.get("contributors", 1)

    if commits == 0 and open_issues > 50 and closed == 0:
        return True, f"No commits in 90 days, {open_issues} unresolved issues, {contributors} contributor(s) — likely abandoned", "high"
    if commits < 5 and contributors <= 1:
        return True, f"Low activity: {commits} commits/90d, single maintainer", "medium"
    return False, f"Active: {commits} commits/90d, {contributors} contributors", "info"


class MaintainerTrustSkill(InvestigationSkill):
    id = "MaintainerTrustSkill"
    name = "Maintainer Trust Analysis"
    description = "Evaluates maintainer activity, commit patterns, and issue responsiveness"
    trigger_conditions = ["abandoned", "maintainer", "activity", "bus factor"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["maintainer_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        mcp_client = ctx.services.get("mcp_client")
        data = await _fetch_repo_data(ctx.dep_name, mcp_client)

        is_concerning, signal, severity = _assess_health(data)
        confidence = 0.75 if data else 0.2

        return [Evidence(
            id=uuid.uuid4().hex,
            kind="maintainer_signal",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            collected_at=datetime.now(UTC).isoformat(),
            signal=signal,
            raw_data=data,
            source="github_mcp",
            reliability=0.8 if data else 0.3,
            confidence=confidence,
            severity=severity,
            supports_hypothesis=is_concerning,
        )]
```

- [ ] **Step 4: Implement ReleaseAnomalySkill**

```python
# src/main_graph/skills/release_anomaly.py
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


async def _fetch_releases(dep_name: str, mcp_client) -> list[dict]:
    try:
        result = await mcp_client.call_tool("get_releases", {"package": dep_name})
        return result or []
    except Exception:
        logger.warning("ReleaseAnomalySkill: MCP fetch failed for %s", dep_name)
        return []


def _detect_anomaly(releases: list[dict]) -> tuple[bool, str]:
    if len(releases) < 2:
        return False, "Insufficient release history"
    rapid = [r for r in releases if r.get("days_since_previous", 999) <= 3]
    if len(rapid) >= 3:
        return True, f"{len(rapid)} releases published within 3 days of each other — suspicious publish cadence"
    return False, f"{len(releases)} releases with normal cadence"


class ReleaseAnomalySkill(InvestigationSkill):
    id = "ReleaseAnomalySkill"
    name = "Release Anomaly Detection"
    description = "Detects suspicious release patterns: rapid publishing, version gaps, ownership changes"
    trigger_conditions = ["release", "version", "publish", "anomaly", "typosquat"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["release_anomaly"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        mcp_client = ctx.services.get("mcp_client")
        releases = await _fetch_releases(ctx.dep_name, mcp_client)
        is_anomalous, signal = _detect_anomaly(releases)

        return [Evidence(
            id=uuid.uuid4().hex,
            kind="release_anomaly",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            collected_at=datetime.now(UTC).isoformat(),
            signal=signal,
            raw_data={"releases": releases},
            source="github_mcp",
            reliability=0.75,
            confidence=0.7 if releases else 0.2,
            severity="high" if is_anomalous else "info",
            supports_hypothesis=is_anomalous,
        )]
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/skills/test_maintainer_skills.py -v
```
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/skills/maintainer_trust.py src/main_graph/skills/release_anomaly.py tests/unit/skills/test_maintainer_skills.py
git commit -m "feat: implement MaintainerTrustSkill and ReleaseAnomalySkill"
```

---

### Task 11: SupplyChainSkill, EcosystemSkill, BlastRadiusSkill

**Files:**
- Modify: `src/main_graph/skills/supply_chain.py`
- Modify: `src/main_graph/skills/ecosystem.py`
- Modify: `src/main_graph/skills/blast_radius.py`
- Create: `tests/unit/skills/test_supply_chain_skill.py`
- Create: `tests/unit/skills/test_blast_radius_skill.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/skills/test_supply_chain_skill.py
from unittest.mock import AsyncMock, patch

from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.supply_chain import SupplyChainSkill
from src.main_graph.skills.ecosystem import EcosystemSkill


def _make_ctx(dep="lodash"):
    return SkillContext(
        dep_name=dep,
        hypothesis_id="h1",
        hypothesis=f"{dep} may be a supply chain risk",
        sbom={},
        concern="supply chain",
        services={"mcp_client": AsyncMock()},
    )


async def test_supply_chain_suspicious_package():
    ctx = _make_ctx()
    skill = SupplyChainSkill()

    with patch("src.main_graph.skills.supply_chain._fetch_registry_metadata") as mock:
        mock.return_value = {
            "has_install_scripts": True,
            "owner_changed_recently": True,
            "name_similarity_score": 0.95,
        }
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].kind == "supply_chain_signal"
    assert evidence[0].supports_hypothesis is True


async def test_ecosystem_healthy_package():
    ctx = _make_ctx()
    skill = EcosystemSkill()

    with patch("src.main_graph.skills.ecosystem._fetch_ecosystem_data") as mock:
        mock.return_value = {
            "weekly_downloads": 10_000_000,
            "dependents": 50_000,
            "stars": 58_000,
        }
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].kind == "ecosystem_signal"
    assert evidence[0].supports_hypothesis is False  # healthy → does not support risk hypothesis
```

```python
# tests/unit/skills/test_blast_radius_skill.py
from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.blast_radius import BlastRadiusSkill


def _sbom_with_deps():
    return {
        "components": [
            {"name": "lodash", "version": "4.17.20"},
            {"name": "express", "version": "4.18.0"},
            {"name": "body-parser", "version": "1.20.0"},
        ],
        "dependencies": [
            {"ref": "express", "dependsOn": ["lodash", "body-parser"]},
            {"ref": "body-parser", "dependsOn": ["lodash"]},
        ],
    }


async def test_blast_radius_high_fanout():
    ctx = SkillContext(
        dep_name="lodash",
        hypothesis_id="h1",
        hypothesis="lodash has high blast radius",
        sbom=_sbom_with_deps(),
        concern="blast radius",
        services={},
    )
    skill = BlastRadiusSkill()
    evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "blast_radius_signal"
    assert "2" in ev.signal or "express" in ev.signal or "body-parser" in ev.signal


async def test_blast_radius_no_dependents():
    ctx = SkillContext(
        dep_name="some-leaf-dep",
        hypothesis_id="h1",
        hypothesis="some-leaf-dep has blast radius",
        sbom={"components": [], "dependencies": []},
        concern="blast radius",
        services={},
    )
    skill = BlastRadiusSkill()
    evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].supports_hypothesis is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/skills/test_supply_chain_skill.py tests/unit/skills/test_blast_radius_skill.py -v
```
Expected: all fail

- [ ] **Step 3: Implement SupplyChainSkill**

```python
# src/main_graph/skills/supply_chain.py
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


async def _fetch_registry_metadata(dep_name: str, mcp_client) -> dict:
    try:
        return await mcp_client.call_tool("get_registry_metadata", {"package": dep_name}) or {}
    except Exception:
        logger.warning("SupplyChainSkill: MCP fetch failed for %s", dep_name)
        return {}


def _assess_supply_chain(meta: dict) -> tuple[bool, str, str]:
    flags = []
    if meta.get("has_install_scripts"):
        flags.append("install scripts present")
    if meta.get("owner_changed_recently"):
        flags.append("recent ownership change")
    if meta.get("name_similarity_score", 0) < 0.8:
        flags.append("name similarity to popular package (possible typosquat)")
    if flags:
        return True, f"Supply chain risk indicators: {'; '.join(flags)}", "high"
    return False, "No supply chain anomalies detected", "info"


class SupplyChainSkill(InvestigationSkill):
    id = "SupplyChainSkill"
    name = "Supply Chain Integrity Assessment"
    description = "Checks provenance, install scripts, typosquatting indicators"
    trigger_conditions = ["supply chain", "provenance", "typosquat", "install script", "compromise"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["supply_chain_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        mcp_client = ctx.services.get("mcp_client")
        meta = await _fetch_registry_metadata(ctx.dep_name, mcp_client)
        is_risky, signal, severity = _assess_supply_chain(meta)

        return [Evidence(
            id=uuid.uuid4().hex,
            kind="supply_chain_signal",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            collected_at=datetime.now(UTC).isoformat(),
            signal=signal,
            raw_data=meta,
            source="npm_registry",
            reliability=0.8,
            confidence=0.75 if meta else 0.2,
            severity=severity,
            supports_hypothesis=is_risky,
        )]
```

- [ ] **Step 4: Implement EcosystemSkill**

```python
# src/main_graph/skills/ecosystem.py
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


async def _fetch_ecosystem_data(dep_name: str, mcp_client) -> dict:
    try:
        return await mcp_client.call_tool("get_ecosystem_metrics", {"package": dep_name}) or {}
    except Exception:
        logger.warning("EcosystemSkill: MCP fetch failed for %s", dep_name)
        return {}


class EcosystemSkill(InvestigationSkill):
    id = "EcosystemSkill"
    name = "Ecosystem Reputation Analysis"
    description = "Assesses npm download trends, community health, and package popularity signals"
    trigger_conditions = ["popularity", "downloads", "community", "ecosystem", "reputation"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["ecosystem_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        mcp_client = ctx.services.get("mcp_client")
        data = await _fetch_ecosystem_data(ctx.dep_name, mcp_client)

        downloads = data.get("weekly_downloads", 0)
        dependents = data.get("dependents", 0)
        is_niche = downloads < 1000 and dependents < 10
        signal = (
            f"{ctx.dep_name} is niche: {downloads} weekly downloads, {dependents} dependents"
            if is_niche
            else f"{ctx.dep_name} is well-adopted: {downloads} weekly downloads"
        )

        return [Evidence(
            id=uuid.uuid4().hex,
            kind="ecosystem_signal",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            collected_at=datetime.now(UTC).isoformat(),
            signal=signal,
            raw_data=data,
            source="npm_registry",
            reliability=0.85,
            confidence=0.7 if data else 0.2,
            severity="medium" if is_niche else "info",
            supports_hypothesis=is_niche,
        )]
```

- [ ] **Step 5: Implement BlastRadiusSkill**

```python
# src/main_graph/skills/blast_radius.py
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.main_graph.subgraphs.ingestion_subgraphs.impact.tools.sbom_tools import compute_blast_radius
from src.models.evidence import Evidence


class BlastRadiusSkill(InvestigationSkill):
    id = "BlastRadiusSkill"
    name = "Blast Radius Estimation"
    description = "Computes transitive dependents and graph depth to estimate change impact"
    trigger_conditions = ["blast radius", "transitive", "impact", "fanout", "graph"]
    required_inputs = ["dep_name", "sbom"]
    evidence_kinds = ["blast_radius_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        if not ctx.dep_name or not ctx.sbom:
            return []

        result = compute_blast_radius(ctx.dep_name, ctx.sbom)
        direct = result.get("direct_dependents", [])
        transitive = result.get("transitive_dependents", [])
        total = len(direct) + len(transitive)
        is_high = total >= 5

        signal = (
            f"{ctx.dep_name} affects {len(direct)} direct and {len(transitive)} transitive packages"
            if total > 0
            else f"{ctx.dep_name} has no dependents in the project graph"
        )

        return [Evidence(
            id=uuid.uuid4().hex,
            kind="blast_radius_signal",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            collected_at=datetime.now(UTC).isoformat(),
            signal=signal,
            raw_data=result,
            source="sbom_graph",
            reliability=0.9,
            confidence=0.85,
            severity="high" if is_high else "low",
            supports_hypothesis=is_high,
        )]
```

- [ ] **Step 6: Run all skill tests**

```bash
uv run pytest tests/unit/skills/ -v
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/main_graph/skills/supply_chain.py src/main_graph/skills/ecosystem.py src/main_graph/skills/blast_radius.py tests/unit/skills/
git commit -m "feat: implement SupplyChainSkill, EcosystemSkill, and BlastRadiusSkill"
```

---

## Phase 3 — Graph Rewire

### Task 12: investigation_planner node

**Files:**
- Create: `src/main_graph/nodes/investigation_planner.py`
- Create: `src/main_graph/nodes/investigation_planner_service.py`
- Create: `tests/unit/nodes/test_investigation_planner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/nodes/test_investigation_planner.py
import json
from unittest.mock import AsyncMock, patch

from src.models.investigation_plan import InvestigationPlan
from src.main_graph.nodes.investigation_planner_service import _run_planner


async def test_run_planner_returns_investigation_plan():
    state = {
        "concern": "security audit",
        "discovery_summary": "React app with 50 deps",
        "sbom_cyclonedx": {"components": [{"name": "lodash"}, {"name": "express"}]},
    }
    llm_response = {
        "hypotheses": [{
            "id": "h1",
            "dep_name": "lodash",
            "statement": "lodash may expose prototype pollution",
            "risk_theme": "vulnerability",
            "rationale": "known CVEs",
            "skills": ["VulnerabilitySkill"],
        }],
        "rationale": "security focus",
        "dep_filter": None,
    }

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AsyncMock(content=json.dumps(llm_response))

    with patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm):
        plan = await _run_planner(state)

    assert isinstance(plan, InvestigationPlan)
    assert len(plan.hypotheses) == 1
    assert plan.hypotheses[0].dep_name == "lodash"
    assert len(plan.skill_plan) == 1
    assert plan.skill_plan[0].skill_id == "VulnerabilitySkill"
    assert plan.rationale == "security focus"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/nodes/test_investigation_planner.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement the service**

```python
# src/main_graph/nodes/investigation_planner_service.py
"""Investigation planner business logic."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command, interrupt

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.domain.ports.vector_store_port import VectorStorePort
from src.main_graph.skills.registry import SKILL_DESCRIPTIONS, SKILL_REGISTRY
from src.main_graph.state import MainState
from src.models.hypothesis import Hypothesis
from src.models.investigation_plan import InvestigationPlan, SkillAssignment
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4)

_PLANNER_SYSTEM = """\
You are a dependency risk investigation planner.

Given a project's SBOM and a user concern, you must:
1. Generate risk hypotheses for the most relevant dependencies.
   Each hypothesis is a falsifiable statement about a specific risk.
   Example: "lodash@4.17.20 may expose the project to prototype pollution attacks"
2. Assign investigation skills to each hypothesis.
   Choose skills whose trigger_conditions match the hypothesis risk_theme.
3. Explain your rationale.

Available skills:
{skill_descriptions}

Output ONLY a valid JSON object:
{{
  "hypotheses": [
    {{
      "id": "h1",
      "dep_name": "<package name>",
      "statement": "<falsifiable risk statement>",
      "risk_theme": "<vulnerability|supply_chain|maintainer|license|reachability|blast_radius>",
      "rationale": "<why this hypothesis>",
      "skills": ["<SkillId>"]
    }}
  ],
  "dep_filter": null,
  "rationale": "<overall plan rationale>"
}}
"""

_INTENT_SYSTEM = """\
Classify the user's response to a proposed investigation plan as one of:
  - approve: user is satisfied and wants to proceed
  - change: user wants modifications
  - cancel: user wants to abort

Return ONLY one word: approve, change, or cancel.
"""


def _build_skill_descriptions() -> str:
    return "\n".join(f"- {sid}: {desc}" for sid, desc in SKILL_DESCRIPTIONS.items())


def _parse_investigation_plan(parsed: dict, concern: str) -> InvestigationPlan:
    hypotheses = [
        Hypothesis(
            id=h["id"],
            dep_name=h["dep_name"],
            statement=h["statement"],
            risk_theme=h["risk_theme"],
            rationale=h["rationale"],
            skills=h["skills"],
        )
        for h in parsed.get("hypotheses", [])
    ]
    skill_plan = [
        SkillAssignment(dep_name=h.dep_name, hypothesis_id=h.id, skill_id=sid)
        for h in hypotheses
        for sid in h.skills
        if sid in SKILL_REGISTRY
    ]
    return InvestigationPlan(
        concern=concern,
        hypotheses=hypotheses,
        skill_plan=skill_plan,
        rationale=parsed.get("rationale", ""),
        dep_filter=parsed.get("dep_filter"),
    )


def _present_plan(plan: InvestigationPlan) -> str:
    lines = ["**Proposed Investigation Plan:**\n", f"*{plan.rationale}*\n"]
    for i, h in enumerate(plan.hypotheses, 1):
        skill_names = [SKILL_REGISTRY[sid].name for sid in h.skills if sid in SKILL_REGISTRY]
        lines.append(f"{i}. **{h.dep_name}**: {h.statement}")
        lines.append(f"   Skills: {', '.join(skill_names)}")
    if plan.dep_filter:
        lines.append(f"\n**Scope:** {', '.join(plan.dep_filter)}")
    lines.append("\nWould you like to proceed, request changes, or cancel?")
    return "\n".join(lines)


async def _run_planner(state: MainState, extra_instructions: str = "") -> InvestigationPlan:
    concern = state.get("concern", "")
    summary = state.get("discovery_summary", "")
    sbom = state.get("sbom_cyclonedx", {})
    components = sbom.get("components", [])
    comp_list = ", ".join(c["name"] for c in components[:30])
    if len(components) > 30:
        comp_list += f", and {len(components) - 30} more"

    user_msg = (
        f"Concern: {concern}\n"
        f"Discovery summary: {summary}\n"
        f"Components ({len(components)}): {comp_list}"
    )
    if extra_instructions:
        user_msg += f"\n\nAdditional instructions: {extra_instructions}"

    response = await _llm.ainvoke([
        {"role": "system", "content": _PLANNER_SYSTEM.format(skill_descriptions=_build_skill_descriptions())},
        {"role": "user", "content": user_msg},
    ])
    parsed = parse_llm_json(response.content or "")
    return _parse_investigation_plan(parsed, concern)


async def _classify_intent(plan: InvestigationPlan, user_input: str) -> str:
    plan_str = "\n".join(f"{i+1}. {h.statement}" for i, h in enumerate(plan.hypotheses))
    response = await _llm.ainvoke([
        {"role": "system", "content": _INTENT_SYSTEM},
        {"role": "user", "content": f"Plan:\n{plan_str}\n\nUser: {user_input}"},
    ])
    intent = response.content.strip().lower()
    return intent if intent in ("approve", "change", "cancel") else "change"


async def investigation_planner_service(
    state: MainState,
    dao: JobRepositoryPort,
    vector_store: VectorStorePort,
) -> dict | Command:
    job_id = state["job_id"]
    plan = await _run_planner(state)

    while True:
        assistant_msg = _present_plan(plan)
        created_at = datetime.now(UTC).isoformat()

        await dao.push_proposal(job_id, {
            "created_at": created_at,
            "plan": {"hypotheses": [h.__dict__ for h in plan.hypotheses], "rationale": plan.rationale},
            "assistant_message": assistant_msg,
        })

        user_input: str = interrupt({
            "investigation_plan": plan.__dict__,
            "assistant_message": assistant_msg,
            "discovery_summary": state.get("discovery_summary", ""),
            "components_count": len(state.get("sbom_cyclonedx", {}).get("components", [])),
        })

        try:
            await vector_store.add_texts([f"Assistant: {assistant_msg}", f"User: {user_input}"])
        except Exception:
            logger.warning("investigation_planner: vector store add failed")

        intent = await _classify_intent(plan, user_input)
        await dao.update_proposal(job_id, created_at=created_at, user_response=user_input, intent=intent)

        new_messages = [AIMessage(content=assistant_msg), HumanMessage(content=user_input)]

        if intent == "approve":
            new_messages.append(AIMessage(content="Plan approved! Investigation is starting now."))
            return {"investigation_plan": plan, "messages": new_messages}

        if intent == "cancel":
            return Command(goto=END, update={"cancelled": True, "messages": new_messages})

        plan = await _run_planner(state, extra_instructions=user_input)
```

- [ ] **Step 4: Create the node**

```python
# src/main_graph/nodes/investigation_planner.py
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from src.main_graph.config import get_services
from src.main_graph.nodes.investigation_planner_service import investigation_planner_service
from src.main_graph.state import MainState


async def investigation_planner(state: MainState, config: RunnableConfig) -> dict | Command:
    svc = get_services(config)
    return await investigation_planner_service(state, svc["job_repo"], svc["vector_store"])
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/nodes/test_investigation_planner.py -v
```
Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/nodes/investigation_planner.py src/main_graph/nodes/investigation_planner_service.py tests/unit/nodes/test_investigation_planner.py
git commit -m "feat: add investigation_planner node"
```

---

### Task 13: skill_dispatcher and skill_executor nodes

**Files:**
- Create: `src/main_graph/nodes/skill_dispatcher.py`
- Create: `src/main_graph/nodes/skill_executor.py`
- Create: `tests/unit/nodes/test_skill_dispatcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/nodes/test_skill_dispatcher.py
from langgraph.types import Send

from src.main_graph.nodes.skill_dispatcher import skill_dispatcher
from src.models.hypothesis import Hypothesis
from src.models.investigation_plan import InvestigationPlan, SkillAssignment


def _make_state(skill_ids: list[str], dep: str = "lodash") -> dict:
    h = Hypothesis(id="h1", dep_name=dep, statement="test", risk_theme="vulnerability", rationale="r", skills=skill_ids)
    plan = InvestigationPlan(
        concern="security",
        hypotheses=[h],
        skill_plan=[SkillAssignment(dep_name=dep, hypothesis_id="h1", skill_id=sid) for sid in skill_ids],
        rationale="test",
    )
    return {
        "investigation_plan": plan,
        "repo_path": "/tmp/repo",
        "sbom_cyclonedx": {},
        "concern": "security",
    }


def test_dispatcher_emits_send_per_assignment():
    state = _make_state(["VulnerabilitySkill", "LicenseSkill"])
    sends = skill_dispatcher(state)
    assert len(sends) == 2
    assert all(isinstance(s, Send) for s in sends)
    assert all(s.node == "skill_executor" for s in sends)


def test_dispatcher_skips_unknown_skill():
    state = _make_state(["VulnerabilitySkill", "NonExistentSkill"])
    sends = skill_dispatcher(state)
    assert len(sends) == 1
    assert sends[0].arg["current_skill_id"] == "VulnerabilitySkill"


def test_dispatcher_skips_skill_when_required_inputs_missing():
    # VulnerabilitySkill requires repo_path
    state = _make_state(["VulnerabilitySkill"])
    state["repo_path"] = None
    sends = skill_dispatcher(state)
    assert len(sends) == 0
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/nodes/test_skill_dispatcher.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement skill_dispatcher**

```python
# src/main_graph/nodes/skill_dispatcher.py
from __future__ import annotations

from langgraph.types import Send

from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.registry import SKILL_REGISTRY
from src.main_graph.state import MainState


def _build_context_for_check(state: MainState, dep_name: str) -> SkillContext:
    return SkillContext(
        dep_name=dep_name,
        hypothesis_id="",
        hypothesis="",
        sbom=state.get("sbom_cyclonedx") or {},
        concern=state.get("concern", ""),
        repo_path=state.get("repo_path"),
        services={},
    )


def skill_dispatcher(state: MainState) -> list[Send]:
    plan = state.get("investigation_plan")
    if plan is None:
        return []

    sends = []
    for assignment in plan.skill_plan:
        skill = SKILL_REGISTRY.get(assignment.skill_id)
        if skill is None:
            continue
        check_ctx = _build_context_for_check(state, assignment.dep_name)
        if not skill.can_run(check_ctx):
            continue
        sends.append(Send("skill_executor", {
            **state,
            "current_skill_id": assignment.skill_id,
            "current_dep_name": assignment.dep_name,
            "current_hypothesis_id": assignment.hypothesis_id,
            "evidence": [],
        }))
    return sends
```

- [ ] **Step 4: Implement skill_executor**

```python
# src/main_graph/nodes/skill_executor.py
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.registry import SKILL_REGISTRY
from src.main_graph.state import MainState
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


def _find_hypothesis_statement(state: MainState, hypothesis_id: str) -> str:
    plan = state.get("investigation_plan")
    if plan is None:
        return ""
    for h in plan.hypotheses:
        if h.id == hypothesis_id:
            return h.statement
    return ""


async def skill_executor(state: MainState, config: RunnableConfig) -> dict:
    skill_id = state.get("current_skill_id", "")
    dep_name = state.get("current_dep_name", "")
    hypothesis_id = state.get("current_hypothesis_id", "")

    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        logger.warning("skill_executor: unknown skill_id=%s", skill_id)
        return {"evidence": []}

    svc = get_services(config)
    ctx = SkillContext(
        dep_name=dep_name,
        hypothesis_id=hypothesis_id,
        hypothesis=_find_hypothesis_statement(state, hypothesis_id),
        sbom=state.get("sbom_cyclonedx") or {},
        repo_path=state.get("repo_path"),
        concern=state.get("concern", ""),
        services=svc,
    )

    try:
        evidence: list[Evidence] = await skill.execute(ctx)
    except Exception:
        logger.exception("skill_executor: skill=%s dep=%s failed", skill_id, dep_name)
        evidence = []

    logger.info("skill_executor: skill=%s dep=%s evidence_count=%d", skill_id, dep_name, len(evidence))
    return {"evidence": evidence}
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/nodes/test_skill_dispatcher.py -v
```
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/nodes/skill_dispatcher.py src/main_graph/nodes/skill_executor.py tests/unit/nodes/test_skill_dispatcher.py
git commit -m "feat: add skill_dispatcher and skill_executor nodes"
```

---

### Task 14: evidence_collector node

**Files:**
- Create: `src/main_graph/nodes/evidence_collector.py`

- [ ] **Step 1: Implement** (no separate test needed — logic is trivial passthrough; tested via integration)

```python
# src/main_graph/nodes/evidence_collector.py
"""Fan-in node — all skill_executor outputs have been reduced into state.evidence by this point."""
import logging

from src.main_graph.state import MainState

logger = logging.getLogger(__name__)


def evidence_collector(state: MainState) -> dict:
    evidence = state.get("evidence") or []
    logger.info("evidence_collector: %d evidence items collected", len(evidence))
    return {}
```

- [ ] **Step 2: Commit**

```bash
git add src/main_graph/nodes/evidence_collector.py
git commit -m "feat: add evidence_collector fan-in node"
```

---

### Task 15: evidence_correlator node

**Files:**
- Create: `src/main_graph/nodes/evidence_correlator.py`
- Create: `tests/unit/nodes/test_evidence_correlator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/nodes/test_evidence_correlator.py
import json
from unittest.mock import AsyncMock, patch

from src.main_graph.nodes.evidence_correlator import (
    _detect_contradictions,
    _group_by_dep,
)
from src.models.evidence import Evidence
from src.models.hypothesis import Hypothesis
from src.models.investigation_plan import InvestigationPlan, SkillAssignment


def _ev(kind, dep, skill, confidence, reliability, supports, severity=None):
    return Evidence(
        kind=kind, dep_name=dep, skill_id=skill, hypothesis_id="h1",
        signal="s", raw_data={}, source="test",
        reliability=reliability, confidence=confidence,
        supports_hypothesis=supports, severity=severity,
    )


def test_group_by_dep():
    evs = [
        _ev("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True),
        _ev("license_signal", "lodash", "LicenseSkill", 0.7, 0.8, False),
        _ev("vulnerability", "express", "VulnerabilitySkill", 0.5, 0.9, True),
    ]
    grouped = _group_by_dep(evs)
    assert len(grouped["lodash"]) == 2
    assert len(grouped["express"]) == 1


def test_detect_contradictions_vuln_unreachable():
    vuln_ev = _ev("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True, "high")
    reach_ev = _ev("reachability_signal", "lodash", "ReachabilitySkill", 0.82, 0.8, True)
    # ReachabilitySkill supports_hypothesis=True means dep is UNREACHABLE

    contradictions = _detect_contradictions([vuln_ev, reach_ev])

    assert len(contradictions) == 1
    c = contradictions[0]
    assert "lodash" in c.description
    assert c.resolution == "effective_risk_reduced"
    assert c.adjusted_confidence < 0.5


def test_detect_contradictions_no_contradiction():
    vuln_ev = _ev("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True, "high")
    # No reachability evidence saying dep is unreachable
    contradictions = _detect_contradictions([vuln_ev])
    assert len(contradictions) == 0
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/nodes/test_evidence_correlator.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/main_graph/nodes/evidence_correlator.py
from __future__ import annotations

import logging

from src.main_graph.state import MainState
from src.main_graph.utils.confidence import compute_confidence, compute_risk_score, compute_severity
from src.models.evidence import Evidence
from src.models.hypothesis import Hypothesis
from src.models.risk_finding import ContradictionReport, RiskFinding
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SYNTHESIS_SYSTEM = """\
You are a dependency risk synthesis engine. Given structured evidence for a dependency, produce a concise risk assessment.

The risk_score and confidence are already computed — do NOT change them.
Output ONLY a JSON object:
{
  "summary": "<2-3 sentence assessment of the risk>",
  "recommendation": "<action to take, or null>",
  "alternatives": ["<maintained alternative package>"]
}
"""


def _group_by_dep(evidence: list[Evidence]) -> dict[str, list[Evidence]]:
    result: dict[str, list[Evidence]] = {}
    for e in evidence:
        result.setdefault(e.dep_name, []).append(e)
    return result


def _detect_contradictions(evidence: list[Evidence]) -> list[ContradictionReport]:
    contradictions = []
    by_dep = _group_by_dep(evidence)

    for dep_name, evs in by_dep.items():
        vuln_evs = [
            e for e in evs
            if e.kind == "vulnerability" and e.supports_hypothesis
            and e.severity in ("critical", "high")
        ]
        # ReachabilitySkill with supports_hypothesis=True means dep is NOT reachable
        unreachable_evs = [
            e for e in evs
            if e.kind == "reachability_signal" and e.supports_hypothesis
        ]

        if vuln_evs and unreachable_evs:
            all_ids = [e.id for e in vuln_evs + unreachable_evs]
            max_conf = max(e.confidence for e in vuln_evs)
            contradictions.append(ContradictionReport(
                evidence_ids=all_ids,
                description=f"{dep_name}: high-severity vulnerability but dependency appears unreachable",
                resolution="effective_risk_reduced",
                adjusted_confidence=max_conf * 0.35,
            ))

    return contradictions


async def _synthesize_finding(
    dep_name: str,
    evs: list[Evidence],
    hypotheses: list[Hypothesis],
    risk_score: float,
    confidence: float,
    severity: str,
    contradictions: list[ContradictionReport],
    concern: str,
) -> RiskFinding:
    dep_hyps = [h for h in hypotheses if h.dep_name == dep_name]
    evidence_summary = "\n".join(f"- [{e.skill_id}] {e.signal}" for e in evs[:10])
    contradiction_summary = "\n".join(f"- {c.description}" for c in contradictions) or "None"

    response = await _llm.ainvoke([
        {"role": "system", "content": _SYNTHESIS_SYSTEM},
        {"role": "user", "content": (
            f"Dependency: {dep_name}\n"
            f"Concern: {concern}\n"
            f"Risk score: {risk_score}/10 (confidence: {confidence:.2f})\n"
            f"Severity: {severity}\n"
            f"Evidence:\n{evidence_summary}\n"
            f"Contradictions:\n{contradiction_summary}"
        )},
    ])

    from src.utils.llm import parse_llm_json
    parsed = parse_llm_json(response.content or "{}")

    return RiskFinding(
        dep_name=dep_name,
        risk_score=risk_score,
        confidence=confidence,
        severity=severity,
        hypotheses=dep_hyps,
        supporting_evidence=[e.id for e in evs if e.supports_hypothesis],
        contradictions=contradictions,
        missing_evidence=[],
        summary=parsed.get("summary", ""),
        recommendation=parsed.get("recommendation"),
        alternatives=parsed.get("alternatives", []),
    )


async def evidence_correlator(state: MainState) -> dict:
    evidence = state.get("evidence") or []
    plan = state.get("investigation_plan")
    concern = state.get("concern", "")
    hypotheses = plan.hypotheses if plan else []

    by_dep = _group_by_dep(evidence)
    contradictions = _detect_contradictions(evidence)

    findings = []
    for dep_name, evs in by_dep.items():
        dep_contradictions = [
            c for c in contradictions
            if any(eid in {e.id for e in evs} for eid in c.evidence_ids)
        ]
        confidence = compute_confidence(evidence, dep_contradictions, dep_name)
        severity = compute_severity(evs)
        risk_score = compute_risk_score(severity, confidence)

        finding = await _synthesize_finding(
            dep_name, evs, hypotheses, risk_score, confidence, severity,
            dep_contradictions, concern,
        )
        findings.append(finding)

    logger.info("evidence_correlator: %d findings, %d contradictions", len(findings), len(contradictions))
    return {
        "risk_findings": findings,
        "contradictions": contradictions,
        "reviewer_feedback": None,
        "review_iterations": (state.get("review_iterations") or 0) + 1,
    }
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/nodes/test_evidence_correlator.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/evidence_correlator.py tests/unit/nodes/test_evidence_correlator.py
git commit -m "feat: add evidence_correlator with contradiction detection and confidence scoring"
```

---

### Task 16: finding_reviewer node and HITL gate 2

**Files:**
- Create: `src/main_graph/nodes/finding_reviewer.py`
- Create: `tests/unit/nodes/test_finding_reviewer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/nodes/test_finding_reviewer.py
from unittest.mock import AsyncMock, patch

from src.main_graph.nodes.finding_reviewer import _check_criteria
from src.models.evidence import Evidence
from src.models.risk_finding import RiskFinding


def _make_finding(dep, score, confidence, severity, evidence_count=2):
    evs = [f"ev{i}" for i in range(evidence_count)]
    return RiskFinding(
        dep_name=dep, risk_score=score, confidence=confidence,
        severity=severity, hypotheses=[], supporting_evidence=evs,
        contradictions=[], missing_evidence=[], summary="test summary",
        recommendation="update package", alternatives=["safer-alt"],
    )


async def test_criteria_pass_when_all_met():
    findings = [_make_finding("lodash", 8.0, 0.8, "high", evidence_count=3)]
    result = await _check_criteria(findings, [])
    assert result["approved"] is True
    assert result["failed_criteria"] == []


async def test_criteria_fail_high_score_low_confidence():
    findings = [_make_finding("lodash", 8.5, 0.3, "high")]
    result = await _check_criteria(findings, [])
    assert result["approved"] is False
    assert any("confidence" in c.lower() for c in result["failed_criteria"])


async def test_criteria_fail_high_sev_no_alternative():
    f = _make_finding("lodash", 8.0, 0.8, "high")
    f.alternatives = []
    f.recommendation = None
    result = await _check_criteria([f], [])
    assert result["approved"] is False
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/nodes/test_finding_reviewer.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/main_graph/nodes/finding_reviewer.py
from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from src.main_graph.state import MainState
from src.models.evidence import Evidence
from src.models.risk_finding import RiskFinding

logger = logging.getLogger(__name__)

_MAX_REVIEW_ITERATIONS = 2


async def _check_criteria(findings: list[RiskFinding], evidence: list[Evidence]) -> dict:
    failed: list[str] = []

    for f in findings:
        if f.severity in ("critical", "high"):
            if len(f.supporting_evidence) < 2:
                failed.append(f"{f.dep_name}: high-severity finding has fewer than 2 supporting evidence items")
            if f.risk_score > 7 and f.confidence < 0.5:
                failed.append(f"{f.dep_name}: risk_score={f.risk_score} but confidence={f.confidence:.2f} — insufficient evidence")
            if not f.alternatives and not f.recommendation:
                failed.append(f"{f.dep_name}: high-risk dependency has no alternative recommendation")

    for f in findings:
        if f.contradictions and not any(
            c.description[:20] in f.summary for c in f.contradictions
        ):
            failed.append(f"{f.dep_name}: contradictions not addressed in summary")

    return {
        "approved": len(failed) == 0,
        "failed_criteria": failed,
        "feedback": "; ".join(failed) if failed else "",
    }


def _format_findings_for_review(findings: list[RiskFinding]) -> str:
    lines = ["**High-Severity Findings Require Your Review:**\n"]
    for f in findings:
        lines.append(f"**{f.dep_name}** — {f.severity.upper()} (score: {f.risk_score}/10, confidence: {f.confidence:.0%})")
        lines.append(f"  {f.summary}")
        if f.recommendation:
            lines.append(f"  Recommendation: {f.recommendation}")
        if f.alternatives:
            lines.append(f"  Alternatives: {', '.join(f.alternatives)}")
        lines.append("")
    lines.append("Please review these findings. Respond to acknowledge or provide additional context.")
    return "\n".join(lines)


async def finding_reviewer(state: MainState) -> dict:
    findings = state.get("risk_findings") or []
    evidence = state.get("evidence") or []
    iterations = state.get("review_iterations") or 0

    review = await _check_criteria(findings, evidence)

    if not review["approved"] and iterations < _MAX_REVIEW_ITERATIONS:
        logger.info("finding_reviewer: criteria failed, requesting re-correlation. feedback=%s", review["feedback"])
        return {"reviewer_feedback": review["feedback"]}

    high_sev = [f for f in findings if f.severity in ("critical", "high")]
    if high_sev:
        assistant_msg = _format_findings_for_review(high_sev)
        user_input: str = interrupt({
            "risk_findings": [f.__dict__ for f in high_sev],
            "assistant_message": assistant_msg,
        })
        logger.info("finding_reviewer: HITL gate 2 — user acknowledged high-severity findings")
        return {
            "review_approved": True,
            "messages": [AIMessage(content=assistant_msg), HumanMessage(content=user_input)],
        }

    return {"review_approved": True}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/nodes/test_finding_reviewer.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/finding_reviewer.py tests/unit/nodes/test_finding_reviewer.py
git commit -m "feat: add finding_reviewer with structured criteria and HITL gate 2"
```

---

### Task 17: report_builder node

**Files:**
- Create: `src/main_graph/nodes/report_builder.py`
- Create: `tests/unit/nodes/test_report_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/nodes/test_report_builder.py
from src.main_graph.nodes.report_builder import report_builder
from src.models.risk_finding import RiskFinding


def _make_finding(dep, score, severity):
    return RiskFinding(
        dep_name=dep, risk_score=score, confidence=0.8,
        severity=severity, hypotheses=[], supporting_evidence=[],
        contradictions=[], missing_evidence=[],
        summary=f"{dep} summary", recommendation="update", alternatives=["alt"],
    )


def test_report_builder_structure():
    state = {
        "concern": "security audit",
        "risk_findings": [
            _make_finding("lodash", 8.5, "high"),
            _make_finding("express", 3.0, "low"),
        ],
        "contradictions": [],
    }
    result = report_builder(state)
    report = result["analysis_report"]

    assert report["concern"] == "security audit"
    assert report["summary"]["total_deps"] == 2
    assert report["summary"]["high"] == 1
    assert report["summary"]["low"] == 1
    assert report["findings"][0]["dep_name"] == "lodash"  # sorted by risk_score desc


def test_report_builder_empty_findings():
    state = {"concern": "test", "risk_findings": [], "contradictions": []}
    result = report_builder(state)
    assert result["analysis_report"]["summary"]["total_deps"] == 0
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/nodes/test_report_builder.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/main_graph/nodes/report_builder.py
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from src.main_graph.state import MainState
from src.models.risk_finding import RiskFinding


def _finding_to_dict(f: RiskFinding) -> dict:
    return {
        "dep_name": f.dep_name,
        "risk_score": f.risk_score,
        "confidence": f.confidence,
        "severity": f.severity,
        "summary": f.summary,
        "recommendation": f.recommendation,
        "alternatives": f.alternatives,
        "supporting_evidence_count": len(f.supporting_evidence),
        "contradictions_count": len(f.contradictions),
        "missing_evidence": f.missing_evidence,
    }


def report_builder(state: MainState) -> dict:
    findings = state.get("risk_findings") or []
    contradictions = state.get("contradictions") or []

    sorted_findings = sorted(findings, key=lambda f: f.risk_score, reverse=True)

    report = {
        "concern": state.get("concern", ""),
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "total_deps": len(findings),
            "critical": sum(1 for f in findings if f.severity == "critical"),
            "high": sum(1 for f in findings if f.severity == "high"),
            "medium": sum(1 for f in findings if f.severity == "medium"),
            "low": sum(1 for f in findings if f.severity == "low"),
        },
        "findings": [_finding_to_dict(f) for f in sorted_findings],
        "contradictions": [
            {"description": c.description, "resolution": c.resolution}
            for c in contradictions
        ],
    }

    return {"analysis_report": report}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/nodes/test_report_builder.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/report_builder.py tests/unit/nodes/test_report_builder.py
git commit -m "feat: add report_builder node (deterministic assembly)"
```

---

### Task 18: Rewire graph.py — connect new backbone

**Files:**
- Modify: `src/main_graph/graph.py`
- Modify: `src/main_graph/constants.py`
- Modify: `src/main_graph/config.py`

- [ ] **Step 1: Update constants**

Read `src/main_graph/constants.py` first, then replace the node name constants with the new set:

```python
# src/main_graph/constants.py
DISCOVERY = "discovery"
INVESTIGATION_PLANNER = "investigation_planner"
SKILL_DISPATCHER = "skill_dispatcher"
SKILL_EXECUTOR = "skill_executor"
EVIDENCE_COLLECTOR = "evidence_collector"
EVIDENCE_CORRELATOR = "evidence_correlator"
FINDING_REVIEWER = "finding_reviewer"
REPORT_BUILDER = "report_builder"
```

- [ ] **Step 2: Update config to expose skill_registry**

Read `src/main_graph/config.py`, then add `skill_registry` to the services dict returned by `get_services()`:

```python
# In get_services(), add:
from src.main_graph.skills.registry import SKILL_REGISTRY
# ...
"skill_registry": SKILL_REGISTRY,
```

- [ ] **Step 3: Rewrite graph.py**

Read `src/main_graph/graph.py` first. Then replace the entire graph construction with:

```python
# src/main_graph/graph.py
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.constants import (
    DISCOVERY,
    EVIDENCE_COLLECTOR,
    EVIDENCE_CORRELATOR,
    FINDING_REVIEWER,
    INVESTIGATION_PLANNER,
    REPORT_BUILDER,
    SKILL_DISPATCHER,
    SKILL_EXECUTOR,
)
from src.main_graph.nodes.evidence_collector import evidence_collector
from src.main_graph.nodes.evidence_correlator import evidence_correlator
from src.main_graph.nodes.finding_reviewer import finding_reviewer
from src.main_graph.nodes.investigation_planner import investigation_planner
from src.main_graph.nodes.report_builder import report_builder
from src.main_graph.nodes.skill_dispatcher import skill_dispatcher
from src.main_graph.nodes.skill_executor import skill_executor
from src.main_graph.state import MainState
from src.main_graph.subgraphs.discovery import build_discovery_graph


def build_main_graph():
    builder = StateGraph(MainState)

    discovery_graph = build_discovery_graph()

    builder.add_node(DISCOVERY, discovery_graph)
    builder.add_node(INVESTIGATION_PLANNER, investigation_planner)
    builder.add_node(SKILL_DISPATCHER, skill_dispatcher)
    builder.add_node(SKILL_EXECUTOR, skill_executor)
    builder.add_node(EVIDENCE_COLLECTOR, evidence_collector)
    builder.add_node(EVIDENCE_CORRELATOR, evidence_correlator)
    builder.add_node(FINDING_REVIEWER, finding_reviewer)
    builder.add_node(REPORT_BUILDER, report_builder)

    builder.add_edge(START, DISCOVERY)
    builder.add_edge(DISCOVERY, INVESTIGATION_PLANNER)
    builder.add_conditional_edges(SKILL_DISPATCHER, lambda s: s, [SKILL_EXECUTOR])
    builder.add_edge(INVESTIGATION_PLANNER, SKILL_DISPATCHER)
    builder.add_edge(SKILL_EXECUTOR, EVIDENCE_COLLECTOR)
    builder.add_edge(EVIDENCE_COLLECTOR, EVIDENCE_CORRELATOR)

    builder.add_conditional_edges(
        EVIDENCE_CORRELATOR,
        lambda s: FINDING_REVIEWER,
        [FINDING_REVIEWER],
    )

    def reviewer_route(state: MainState) -> str:
        if state.get("reviewer_feedback") and (state.get("review_iterations") or 0) <= 2:
            return EVIDENCE_CORRELATOR
        return REPORT_BUILDER

    builder.add_conditional_edges(FINDING_REVIEWER, reviewer_route, [EVIDENCE_CORRELATOR, REPORT_BUILDER])
    builder.add_edge(REPORT_BUILDER, END)

    checkpointer = MemorySaver()
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=[INVESTIGATION_PLANNER],
    )


main_graph = build_main_graph()
```

- [ ] **Step 4: Run the full test suite**

```bash
uv run pytest tests/unit/ -v
```
Expected: all new tests pass; existing tests that reference deleted node names will fail — fix those in the next step.

- [ ] **Step 5: Delete old nodes and subgraphs**

Remove files that have been fully replaced:

```bash
rm src/main_graph/nodes/orchestrator.py
rm src/main_graph/nodes/orchestrator_service.py
rm src/main_graph/nodes/planner.py
rm src/main_graph/nodes/execution_planner.py
rm src/main_graph/nodes/execute_plan.py
rm src/main_graph/nodes/execute_plan_service.py
rm src/main_graph/nodes/stage_advance.py
rm src/main_graph/nodes/task_dispatcher.py
rm src/main_graph/nodes/risk_score.py
rm src/main_graph/nodes/risk_ranker.py
rm src/main_graph/nodes/recommendation.py
rm src/main_graph/nodes/recommendation_tools.py
rm -rf src/main_graph/subgraphs/cross_analyzer/
rm -rf src/main_graph/subgraphs/report_reviewer/
rm -rf src/main_graph/subgraphs/ingestion_subgraphs/
rm -f src/main_graph/plan.py
```

- [ ] **Step 6: Delete obsolete tests**

```bash
rm tests/unit/nodes/test_execute_plan.py
rm tests/unit/nodes/test_execution_planner.py
rm tests/unit/nodes/test_planner.py
rm tests/unit/nodes/test_recommendation.py
rm tests/unit/nodes/test_recommendation_tools.py
rm tests/unit/nodes/test_risk_ranker.py
rm tests/unit/nodes/test_risk_score.py
rm tests/unit/subgraphs/test_license_compliance_service.py
rm tests/unit/subgraphs/test_vulnerabilities_service.py
rm -rf tests/unit/subgraphs/impact/
rm tests/unit/test_plan.py
```

- [ ] **Step 7: Run full test suite**

```bash
uv run pytest tests/ -v
```
Expected: all remaining tests pass; 0 failures

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: rewire main graph — 8-node cognitive investigation pipeline replaces old subgraph pipeline"
```

---

### Task 19: Update architecture test boundaries

**Files:**
- Modify: `tests/architecture/test_boundaries.py`

- [ ] **Step 1: Read existing boundaries test**

Read `tests/architecture/test_boundaries.py` to understand the current import rules being enforced.

- [ ] **Step 2: Update to reflect new module structure**

The key boundary to enforce: graph nodes import from `src.main_graph.skills`, never from `src.main_graph.subgraphs.ingestion_subgraphs` (which no longer exists). Update any import path checks in the file to reference the new `skills/` module.

- [ ] **Step 3: Run architecture tests**

```bash
uv run pytest tests/architecture/ -v
```
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add tests/architecture/test_boundaries.py
git commit -m "test: update architecture boundary tests for new skills module structure"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Evidence model with id, kind, dep_name, skill_id, hypothesis_id, signal, raw_data, source, reliability, confidence, severity, supports_hypothesis, contradicts_evidence | Task 1 |
| Hypothesis model with status and confidence | Task 2 |
| InvestigationPlan with hypotheses, skill_plan, rationale | Task 2 |
| RiskFinding with risk_score, confidence, severity, contradictions, missing_evidence | Task 3 |
| Confidence arithmetic (base, penalty, bonus) | Task 4 |
| InvestigationSkill ABC + SkillContext + can_run() | Task 5 |
| 8-skill registry | Task 5 |
| MainState with evidence Annotated reducer | Task 6 |
| LicenseSkill wrapping Trivy license scan | Task 7 |
| VulnerabilitySkill wrapping Trivy vuln scan | Task 8 |
| ReachabilitySkill wrapping impact/find_usages | Task 9 |
| MaintainerTrustSkill + ReleaseAnomalySkill (split repo subgraph) | Task 10 |
| SupplyChainSkill + EcosystemSkill + BlastRadiusSkill | Task 11 |
| investigation_planner with hypothesis generation + HITL gate 1 | Task 12 |
| skill_dispatcher fan-out via Send() with can_run() guard | Task 13 |
| skill_executor wrapping skill.execute() | Task 13 |
| evidence_collector fan-in | Task 14 |
| evidence_correlator with _group_by_dep, _detect_contradictions, confidence arithmetic, LLM synthesis | Task 15 |
| finding_reviewer with structured criteria + HITL gate 2 | Task 16 |
| report_builder deterministic assembly sorted by risk_score | Task 17 |
| graph.py rewired with 8 backbone nodes | Task 18 |
| Old nodes and subgraphs deleted | Task 18 |

**Phase 4 (empirical calibration, confidence weight tuning, thesis evaluation against CodeTech projects, frontend DAG update):** Plan separately after Phase 3 is running end-to-end.
