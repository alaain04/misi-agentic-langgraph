# Cognitive Investigation Platform — Architecture Design

**Date:** 2026-05-22
**Branch:** feat/superpower-investigation
**Status:** Approved

---

## Context

The current system is a LangGraph pipeline that analyzes JavaScript/Node.js dependency risk for CodeTech. It works, but it is execution-oriented: a planner selects named subgraphs, fans them out via `Send()`, collects flat result IDs, and assembles a report through a dict-merge cross-analyzer. Evidence is not typed, confidence is not modeled, and the cross-analyzer cannot detect contradictions.

This design evolves the system into a **cognitive investigation platform** — hypothesis-driven, evidence-first, with deterministic confidence scoring and structured review loops — while preserving the external API contract and the discovery subgraph entirely.

**Thesis alignment:** The redesign directly serves the thesis objective of specifying and evaluating risks introduced by JavaScript dependencies in CodeTech projects using an interactive AI-driven strategy.

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Investigation unit | Per-dependency risk evaluation | Every dep gets assessed; concern modulates depth and dimension |
| Planning model | LLM-driven dynamic planning | Free-form concern → structured InvestigationPlan with hypotheses |
| HITL gates | Dual gate | Plan approval (gate 1) + high-severity finding review (gate 2) |
| Core abstraction | Skill-based investigation + shared evidence store | Skills are reusable, evidence is queryable, confidence is first-class |

---

## Graph Topology

Eight backbone nodes. The graph shape never changes — what varies per investigation is the content of the skill plan. `skill_executor` is one node definition invoked N times via `Send()`, one per `(dep, skill)` pair.

```
START
│
▼
[ discovery ]           unchanged: clone → inspect → SBOM → summary
│
▼
[ investigation_planner ]   LLM reads concern + SBOM → InvestigationPlan
│                           Plan = { hypotheses[], skill_plan[] }
│
▼  interrupt() ← HITL gate 1: user approves plan
│
▼
[ skill_dispatcher ]    fans out Send() per (dep_name, skill_id) pair
│
├─ Send(dep_A, VulnerabilitySkill) ──→ [ skill_executor ]
├─ Send(dep_A, MaintainerTrustSkill) ─→ [ skill_executor ]
├─ Send(dep_B, SupplyChainSkill) ────→ [ skill_executor ]
└─ ...                                        │
                                              ▼
                                   each executor writes Evidence[]
│
▼
[ evidence_collector ]  fan-in: waits for all skill_executors
│
▼
[ evidence_correlator ] correlates signals, detects contradictions,
│                       scores confidence, produces RiskFinding[]
│
▼
[ finding_reviewer ]    structured criteria check + LLM review loop
│
├─ high_severity_found? ──→ interrupt() ← HITL gate 2: human reviews
│
▼
[ report_builder ]      deterministic assembly of final structured report
│
▼
END
```

### Node comparison (current → new)

| Current | New | Key difference |
|---|---|---|
| orchestrator + planner | investigation_planner | Outputs InvestigationPlan with hypotheses, not subgraph names |
| execution_planner + task_dispatcher | skill_dispatcher | Fans out per (dep, skill) pair |
| execute_plan | skill_executor | Calls skill.execute(), writes Evidence[] |
| stage_advance | evidence_collector | Same fan-in, accumulates Evidence[] not result_ids |
| cross_analyzer | evidence_correlator | Correlation + contradiction detection + confidence scoring |
| report_reviewer | finding_reviewer + HITL gate 2 | Structured criteria + human gate on high-severity |
| risk_score + recommendation | absorbed into evidence_correlator | No separate nodes needed |

---

## Core Data Models

All models are defined in `src/models/` as Python dataclasses with Pydantic validation.

### Evidence

The atomic unit of all investigation output. Every skill returns `Evidence[]`, never raw strings or dicts.

```python
@dataclass
class Evidence:
    id:                    str          # uuid
    kind:                  EvidenceKind
    dep_name:              str
    skill_id:              str          # which skill produced this
    hypothesis_id:         str          # which hypothesis it relates to
    collected_at:          str          # ISO timestamp
    signal:                str          # human-readable finding
    raw_data:              dict         # structured source data
    source:                str          # "trivy" | "github_mcp" | "npm_registry" …
    source_url:            str | None
    reliability:           float        # 0–1: how trustworthy is the source
    confidence:            float        # 0–1: how strong is this signal
    severity:              Severity | None
    supports_hypothesis:   bool         # supports or refutes the hypothesis
    contradicts_evidence:  list[str]    # ids of contradicted evidence items

EvidenceKind = Literal[
    "vulnerability", "maintainer_signal", "supply_chain_signal",
    "license_signal", "reachability_signal", "blast_radius_signal",
    "release_anomaly", "ecosystem_signal"
]
```

### Hypothesis

A falsifiable statement about a specific dependency risk. Generated by the planner, resolved by the correlator.

```python
@dataclass
class Hypothesis:
    id:          str
    dep_name:    str          # or "global" for project-wide hypotheses
    statement:   str          # "lodash@4.17.20 may expose prototype pollution attacks"
    risk_theme:  str          # "vulnerability" | "supply_chain" | "maintainer" …
    rationale:   str          # why the planner generated this hypothesis
    skills:      list[str]    # skill ids assigned to investigate this
    status:      HypothesisStatus  # open | supported | refuted | inconclusive
    confidence:  float | None # filled after correlation
```

### InvestigationPlan

Output of `investigation_planner`. Replaces the current `Plan` TypedDict.

```python
@dataclass
class InvestigationPlan:
    concern:     str
    hypotheses:  list[Hypothesis]
    skill_plan:  list[SkillAssignment]  # flat: (dep_name, hypothesis_id, skill_id)
    rationale:   str        # LLM explains why this plan was chosen (auditable)
    dep_filter:  list[str] | None

@dataclass
class SkillAssignment:
    dep_name:      str
    hypothesis_id: str
    skill_id:      str
```

### RiskFinding

Output of `evidence_correlator` per dependency. Replaces the current `analysis_report` dict.

```python
@dataclass
class RiskFinding:
    dep_name:            str
    risk_score:          float        # 0–10
    confidence:          float        # 0–1
    severity:            Severity     # critical | high | medium | low
    hypotheses:          list[Hypothesis]
    supporting_evidence: list[str]    # Evidence ids
    contradictions:      list[ContradictionReport]
    missing_evidence:    list[str]    # evidence kinds we wanted but couldn't gather
    summary:             str
    recommendation:      str | None
    alternatives:        list[str]

@dataclass
class ContradictionReport:
    evidence_ids:        list[str]
    description:         str
    resolution:          str          # "effective_risk_reduced" | "unresolved" …
    adjusted_confidence: float
```

### MainState

Replaces the current flat `MainState` TypedDict. Old execution tracking fields are removed.

```python
class MainState(TypedDict):
    # Inputs
    repo_url:    str
    concern:     str
    job_id:      str

    # Discovery (unchanged)
    repo_path:          NotRequired[str]
    project_metadata:   NotRequired[ProjectMetadata]
    manifest_files:     NotRequired[list[str]]
    discovery_summary:  NotRequired[str]
    discovery_error:    NotRequired[str | None]
    sbom_cyclonedx:     NotRequired[dict]
    sbom_result_id:     NotRequired[str]

    # Investigation plan
    investigation_plan: NotRequired[InvestigationPlan]
    messages:           Annotated[list, add_messages]

    # Evidence (fan-in reducer — replaces subgraph_results)
    evidence: Annotated[list[Evidence], operator.add]

    # Skill execution (Send() fields)
    current_skill_id:      NotRequired[str]
    current_dep_name:      NotRequired[str]
    current_hypothesis_id: NotRequired[str]

    # Correlation outputs
    risk_findings:     NotRequired[list[RiskFinding]]
    contradictions:    NotRequired[list[ContradictionReport]]
    reviewer_feedback: NotRequired[str]
    review_approved:   NotRequired[bool]
    review_iterations: NotRequired[int]

    # Control
    cancelled: NotRequired[bool]
```

---

## Investigation Skills

### Skill contract

Every skill implements `InvestigationSkill`. The `execute()` method never raises — it returns an empty list on failure. The `can_run()` guard prevents silent partial execution.

```python
@dataclass
class SkillContext:
    dep_name:      str
    hypothesis_id: str
    hypothesis:    str      # the hypothesis statement
    sbom:          dict
    repo_path:     str | None
    concern:       str
    services:      dict     # injected: container, mcp_client, daos…

class InvestigationSkill(ABC):
    id:                  str
    name:                str
    description:         str
    trigger_conditions:  list[str]
    required_inputs:     list[str]
    evidence_kinds:      list[EvidenceKind]

    @abstractmethod
    async def execute(self, ctx: SkillContext) -> list[Evidence]: ...

    def can_run(self, ctx: SkillContext) -> bool:
        return all(getattr(ctx, f, None) is not None for f in self.required_inputs)
```

### Skill registry (8 initial skills)

| Skill ID | Name | Trigger conditions | Evidence kinds | Wraps |
|---|---|---|---|---|
| VulnerabilitySkill | Vulnerability Surface Assessment | security, CVE, supply chain | vulnerability | vulnerabilities subgraph |
| MaintainerTrustSkill | Maintainer Trust Analysis | abandoned, single maintainer, low activity | maintainer_signal | repo subgraph (commits, issues) |
| SupplyChainSkill | Supply Chain Integrity Assessment | typosquatting, provenance, install scripts | supply_chain_signal | registry subgraph |
| LicenseSkill | License Compliance Assessment | commercial use, license conflict, copyleft | license_signal | license_compliance subgraph |
| ReachabilitySkill | Reachability Assessment | code impact, tree shaking, unused deps | reachability_signal | impact subgraph |
| BlastRadiusSkill | Blast Radius Estimation | transitive deps, deep graph, high fanout | blast_radius_signal | new |
| ReleaseAnomalySkill | Release Anomaly Detection | rapid releases, version gaps, suspicious publish | release_anomaly | repo subgraph (releases) |
| EcosystemSkill | Ecosystem Reputation Analysis | popularity, downloads, community health | ecosystem_signal | registry subgraph |

```python
# src/main_graph/skills/registry.py
SKILL_REGISTRY: dict[str, InvestigationSkill] = {
    skill.id: skill for skill in [
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

SKILL_DESCRIPTIONS = {
    sid: f"{s.name}: {s.description} | triggers: {', '.join(s.trigger_conditions)}"
    for sid, s in SKILL_REGISTRY.items()
}
```

---

## Cognitive Node Designs

### investigation_planner

Replaces `orchestrator` + `planner`. Reads concern + SBOM, generates hypotheses, assigns skills. Presents the plan to the user via `interrupt()` (HITL gate 1). Re-plans on "change" intent, exits on "cancel".

**Planner LLM prompt (system):**
```
You are a dependency risk investigation planner.

Given a project's SBOM and a user concern, you must:
1. Generate risk hypotheses for the most relevant dependencies.
   Each hypothesis is a falsifiable statement about a specific risk.
   Example: "lodash@4.17.20 may expose the project to prototype pollution attacks"

2. Assign investigation skills to each hypothesis.
   Choose skills whose trigger_conditions match the hypothesis risk theme.

3. Explain your rationale — why these hypotheses, why these skills.

Available skills: {skill_descriptions}

Output: JSON matching InvestigationPlan schema.
```

HITL gate 1 interrupt payload:
```python
interrupt({
    "investigation_plan": plan,
    "assistant_message": _present_plan(plan),  # human-readable hypotheses list
    "discovery_summary": state["discovery_summary"],
    "components_count": len(sbom.get("components", [])),
})
```

### evidence_correlator

Replaces `cross_analyzer`. Four-step pipeline — first three steps are deterministic, last step is LLM-driven.

```python
async def evidence_correlator(state: MainState) -> dict:
    evidence = state["evidence"]
    hypotheses = state["investigation_plan"]["hypotheses"]

    # Step 1 — deterministic grouping (pure function)
    by_dep = _group_by_dep(evidence)
    by_hypothesis = _group_by_hypothesis(evidence)

    # Step 2 — contradiction detection (rule-based)
    contradictions = _detect_contradictions(evidence)
    # Example: VulnSkill says "critical CVE" AND ReachabilitySkill says "dep unreachable"
    # → ContradictionReport { resolution: "effective_risk_reduced", adjusted_confidence: 0.35 }

    # Step 3 — confidence scoring (deterministic arithmetic)
    scores = {
        dep: _compute_confidence(evs, contradictions)
        for dep, evs in by_dep.items()
    }

    # Step 4 — LLM synthesis (narrative only, not score)
    findings = await _synthesize_findings(by_dep, by_hypothesis, scores, contradictions, state["concern"])

    return {"risk_findings": findings, "contradictions": contradictions}
```

**Confidence formula:**
```
base     = weighted_avg(evidence.confidence × evidence.reliability)
penalty  = −0.2 × count(unresolved_contradictions)
penalty += −0.1 × count(missing_required_evidence_kinds)
bonus    = +0.1 × count(corroborated_by_2+_independent_skills)
final    = clamp(base + penalty + bonus, 0.0, 1.0)
```

**Contradiction detection — example:**
```
Evidence A (VulnerabilitySkill):  lodash has CVE-2021-23337, severity=high, confidence=0.95
Evidence B (ReachabilitySkill):   lodash not imported in any execution path, confidence=0.82
→ Contradiction: high-severity vuln in unreachable dependency
→ adjusted_confidence = 0.35 (down from 0.95)
```

### finding_reviewer

Replaces `report_reviewer`. Checks structured criteria — not free-form "does this look good?". Loops back to `evidence_correlator` if any criterion fails. Triggers HITL gate 2 if high-severity findings are present and all criteria pass.

```python
REVIEW_CRITERIA = [
    "Every high-severity finding has ≥2 supporting evidence items",
    "No finding has risk_score > 7 with confidence < 0.5",
    "All contradictions are explicitly addressed in the finding summary",
    "Every high-risk dep has at least one alternative recommendation",
    "Missing evidence is acknowledged, not silently omitted",
]
```

HITL gate 2 interrupt payload:
```python
interrupt({
    "risk_findings": high_severity_findings,
    "contradictions": contradictions,
    "evidence_summary": _summarize_evidence(evidence),  # provenance visible to user
})
```

---

## Deterministic vs LLM-Driven

| Component | Mode | Rationale |
|---|---|---|
| Evidence grouping | Deterministic | Pure function, always reproducible |
| Contradiction detection | Rule-based | Auditable, no hallucination risk |
| Confidence arithmetic | Deterministic | Scores traceable to evidence weights |
| Skill dispatcher fan-out | Deterministic | Mechanical iteration over skill_plan |
| can_run() guard | Deterministic | Prevents silent partial execution |
| Hypothesis generation | LLM | Requires understanding of concern + context |
| Skill selection per hypothesis | LLM | Trigger matching requires semantic reasoning |
| Finding synthesis (summary, recommendation) | LLM | Narrative from structured evidence |
| Review criteria evaluation | LLM | Semantic judgment of coverage and coherence |
| Intent classification (approve/change/cancel) | LLM | Natural language understanding |

---

## Folder Structure

```
src/
├── api/                              # unchanged
├── db/                               # unchanged
├── domain/
│   └── ports/                        # add EvidenceStorePort (future)
├── models/                           # add Evidence, Hypothesis, RiskFinding, InvestigationPlan
├── services/                         # unchanged (job_dao, job_runner)
└── main_graph/
    ├── graph.py                      # rewired — 6 backbone nodes
    ├── state.py                      # new MainState
    ├── constants.py                  # updated node names
    ├── config.py                     # add skill_registry to services
    │
    ├── skills/                       # NEW — replaces ingestion_subgraphs/
    │   ├── base.py                   # InvestigationSkill ABC, SkillContext, Evidence
    │   ├── registry.py               # SKILL_REGISTRY + SKILL_DESCRIPTIONS
    │   ├── vulnerability.py
    │   ├── maintainer_trust.py
    │   ├── supply_chain.py
    │   ├── license.py
    │   ├── reachability.py
    │   ├── blast_radius.py
    │   ├── release_anomaly.py
    │   └── ecosystem.py
    │
    ├── nodes/
    │   ├── investigation_planner.py  # replaces orchestrator + planner
    │   ├── skill_dispatcher.py       # replaces execution_planner + task_dispatcher
    │   ├── skill_executor.py         # replaces execute_plan
    │   ├── evidence_collector.py     # replaces stage_advance
    │   ├── evidence_correlator.py    # replaces cross_analyzer
    │   ├── finding_reviewer.py       # replaces report_reviewer
    │   └── report_builder.py         # new — deterministic assembly of RiskFinding[] into final report dict (no LLM)
    │
    └── subgraphs/
        └── discovery/                # unchanged
```

---

## Migration Strategy

### What to keep, wrap, and rewrite

| Current component | Action | Risk |
|---|---|---|
| discovery subgraph | Keep as-is | none |
| vulnerabilities/service.py | Wrap in VulnerabilitySkill.execute() | low |
| license_compliance/service.py | Wrap in LicenseSkill.execute() | low |
| impact subgraph | Wrap in ReachabilitySkill.execute() | low |
| repo subgraph | Split into MaintainerTrustSkill + ReleaseAnomalySkill | medium |
| registry subgraph | Split into SupplyChainSkill + EcosystemSkill | medium |
| orchestrator + planner | Rewrite as investigation_planner | medium |
| execution_planner + task_dispatcher | Rewrite as skill_dispatcher | low |
| execute_plan | Rewrite as skill_executor | low |
| stage_advance | Rewrite as evidence_collector | low |
| cross_analyzer | Full rewrite as evidence_correlator | high |
| report_reviewer | Rewrite as finding_reviewer | medium |
| risk_score + recommendation nodes | Delete — absorbed into correlator | none |
| MainState | Replace — keep discovery fields, drop execution tracking | medium |
| API layer, job_dao, job_runner | Keep as-is — external contract unchanged | none |

### Safe migration order

1. Data models first (pure Python, no LangGraph dependencies)
2. Skills second (isolated, individually testable)
3. Graph nodes last (rewire once all skills are verified)

The system remains runnable throughout — skills can be tested in isolation before the graph is rewired.

---

## Implementation Roadmap

### Phase 1 — Foundation
Define all data models and the skill base class. No graph changes. System continues running on the old pipeline.

- Define `Evidence`, `Hypothesis`, `InvestigationPlan`, `RiskFinding`, `ContradictionReport` models
- Define `InvestigationSkill` ABC + `SkillContext`
- Implement `SKILL_REGISTRY` with all 8 skills (stubs return empty `Evidence[]` — acceptable for Phase 1)
- Update `MainState` — add new fields alongside old ones for backward compatibility
- Unit tests for Evidence model and confidence arithmetic

**Deliverable:** all data models typed, skill registry wired, state backward-compatible

### Phase 2 — Skills
Migrate existing analyzer logic into skills. Test each skill in isolation.

- `LicenseSkill` (simplest — pure SBOM logic)
- `VulnerabilitySkill` (wrap existing Trivy service)
- `ReachabilitySkill` (wrap impact subgraph)
- `MaintainerTrustSkill` + `ReleaseAnomalySkill` (split repo subgraph)
- `SupplyChainSkill` + `EcosystemSkill` (split registry subgraph)
- `BlastRadiusSkill` (new — dependency graph traversal)
- Integration test: each skill returns valid `Evidence[]` against a real repository

**Deliverable:** all 8 skills implemented and integration-tested

### Phase 3 — Graph Rewire
Replace graph nodes. New pipeline goes live end-to-end.

- `investigation_planner` (new LLM prompt + InvestigationPlan output schema)
- `skill_dispatcher` (Send() per skill_plan entry)
- `skill_executor` (calls skill.execute(), writes Evidence[])
- `evidence_collector` (fan-in)
- `evidence_correlator` (contradiction detection + confidence + LLM synthesis)
- `finding_reviewer` (structured criteria + HITL gate 2)
- `report_builder` (deterministic assembly)
- Rewire `graph.py` — remove old nodes, add new backbone
- End-to-end integration test on a real CodeTech project

**Deliverable:** full new pipeline running end-to-end, old nodes deleted

### Phase 4 — Epistemic Hardening
Strengthen evidence quality and validate the thesis hypothesis empirically.

- Tune confidence weights per evidence kind (empirical calibration on real projects)
- Add missing-evidence tracking to `RiskFinding.missing_evidence[]`
- Implement corroboration bonus in the confidence model
- Add contradiction resolution strategies (beyond detection)
- Enrich HITL gate 2 — expose evidence provenance to the user, not just findings
- Evaluate against CodeTech projects — validate thesis hypothesis
- Frontend: update DAG visualization to show skills + evidence instead of subgraphs

**Deliverable:** system validated on CodeTech projects, thesis evaluation complete

---

## Key Invariants

- The graph topology has exactly 8 backbone nodes and never changes per-investigation; `skill_executor` is one node definition invoked N times via `Send()`
- Adding a new investigation skill requires zero graph changes — register in `SKILL_REGISTRY` only
- Confidence scores are always traceable to specific evidence weights — the LLM cannot inflate them
- Every piece of information flowing through the graph is a typed `Evidence` object with source attribution
- The external API contract (`POST /analyze`, `GET /analyze/{trace_id}`, `POST /analyze/{trace_id}/chat`) is unchanged
- The discovery subgraph is untouched
