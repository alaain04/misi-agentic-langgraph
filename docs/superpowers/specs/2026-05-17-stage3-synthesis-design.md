# Stage 3 — Synthesis Design (risk_ranker, risk_score, recommendation)

**Date:** 2026-05-17
**Status:** Draft — pending brainstorm session
**Parent spec:** [ingestion-subgraphs-design.md](2026-05-17-ingestion-subgraphs-design.md)

---

## Context

Stage 3 is the synthesis phase. Three agentic nodes run after all ingestion and impact data is available:

- **`risk_ranker`** (between Stage 1 and Stage 2) — cross-analyzes all Stage 1 signals, ranks deps by risk, selects the high-risk subset for Stage 2
- **`risk_score`** (Stage 3) — computes the final authoritative 0–10 risk score per dep, incorporating Stage 1 + Stage 2
- **`recommendation`** (Stage 3) — finds 1–3 maintained alternatives for each high-risk dep

All three are fully agentic: LLM bound to typed tools, ReAct loop, structured output.

---

## `risk_ranker`

**Goal:** Cross-analyze all Stage 1 signals for every dep in scope. Produce a preliminary risk ranking and select the high-risk subset that will receive Stage 2 impact analysis.

### Tools

| Tool | Signature | What it does |
|---|---|---|
| `list_analyzed_deps` | `() -> list[str]` | Returns all dep names that have Stage 1 results |
| `get_vulnerabilities` | `(dep_name: str) -> dict` | CVE findings from `vulnerabilities_dao` |
| `get_license_compliance` | `(dep_name: str) -> dict` | License analysis from `license_compliance_dao` |
| `get_registry_data` | `(dep_name: str) -> dict` | npm health from `registry_dao` |
| `get_repo_data` | `(dep_name: str) -> dict` | GitHub signals from `repo_dao` |
| `get_runtime_data` | `(dep_name: str) -> dict` | Test/lint results from `runtime_dao` |
| `save_ranking` | `(rankings: list[dict]) -> None` | Persists the ranked list to `MainState`; each entry: `{dep_name, preliminary_score, risk_signals: list[str], rationale: str}` |

### System Prompt Direction

The agent is instructed to:
1. Retrieve signals for all analyzed deps
2. Reason about cross-domain risk (e.g., a deprecated package with CVEs is higher risk than one with only CVEs)
3. Produce an ordered ranking with rationale per dep
4. Flag the high-risk subset using the selection criteria below
5. Call `save_ranking`

### Selection Criteria for Stage 2

A dep is marked high-risk if any of:
- Any CVE with CRITICAL or HIGH severity
- Flagged deprecated or unmaintained by `registry`
- Failing tests (exit_code ≠ 0) from `runtime`
- Top-3 by preliminary score (fallback — ensures Stage 2 always runs when `impact` is selected)

### Output in `MainState`

```python
risk_rankings: list[dict]    # [{dep_name, preliminary_score, risk_signals, rationale}]
high_risk_deps: list[str]    # deps selected for Stage 2
```

---

## `risk_score`

**Goal:** Compute the final authoritative risk score per dep, incorporating both Stage 1 risk signals and Stage 2 impact findings.

### Tools

| Tool | Signature | What it does |
|---|---|---|
| `get_preliminary_ranking` | `(dep_name: str) -> dict` | risk_ranker preliminary assessment |
| `get_impact_data` | `(dep_name: str) -> dict \| None` | Stage 2 impact analysis (None if not analyzed) |
| `get_vulnerabilities` | `(dep_name: str) -> dict` | CVE findings |
| `get_registry_data` | `(dep_name: str) -> dict` | npm health |
| `get_repo_data` | `(dep_name: str) -> dict` | GitHub signals |
| `get_runtime_data` | `(dep_name: str) -> dict` | Test/lint results |
| `save_risk_score` | `(dep_name: str, score: float, severity: str, breakdown: dict, rationale: str) -> None` | Persists the final score |

### System Prompt Direction

The agent produces a 0–10 score per dep, a severity label, a per-dimension breakdown, and a human-readable rationale. It must call `save_risk_score` for every dep in scope.

### Output Schema

```python
class RiskScoreEntry(BaseModel):
    dep_name: str
    score: float                    # 0–10
    severity: str                   # critical | high | medium | low
    breakdown: dict[str, float]     # {vulnerabilities: 8.5, maintenance: 3.0, ...}
    rationale: str
    impact_weight: float | None     # None if impact not analyzed
```

---

## `recommendation`

**Goal:** For each high-risk dep, find 1–3 actively maintained alternatives and explain the migration trade-off.

### Tools

| Tool | Signature | What it does |
|---|---|---|
| `get_risk_score` | `(dep_name: str) -> dict` | Final risk score and breakdown |
| `get_impact_data` | `(dep_name: str) -> dict \| None` | Usage + blast radius for context |
| `search_npm` | `(query: str, max_results: int = 10) -> list[dict]` | Searches npm registry; returns `[{name, description, weekly_downloads, last_publish}]` |
| `get_npm_metadata` | `(package_name: str) -> dict` | Full npm metadata: version history, maintainers, homepage, repository, license, deprecation |
| `get_github_summary` | `(owner: str, repo: str) -> dict` | GitHub stars, open issues, last commit date, release frequency |
| `compare_packages` | `(original: str, alternatives: list[str]) -> dict` | Side-by-side comparison of download trends, maintenance health, and license |
| `save_recommendation` | `(dep_name: str, risk_summary: str, alternatives: list[dict], migration_notes: str) -> None` | Persists the recommendation |

### System Prompt Direction

The agent is instructed to:
1. Understand the risk profile and current usage of the dep
2. Search for alternatives in the same problem space
3. Evaluate each candidate on maintenance health, downloads, license compatibility, and API similarity
4. Select the top 1–3 and write a migration trade-off note
5. Call `save_recommendation` for each high-risk dep

### Output Schema

```python
class RecommendationEntry(BaseModel):
    dep_name: str
    risk_summary: str
    alternatives: list[AlternativeEntry]
    migration_notes: str

class AlternativeEntry(BaseModel):
    name: str
    reason: str                      # why this is a good alternative
    weekly_downloads: int | None
    last_publish: str | None
    license: str | None
    api_similarity: str              # high | medium | low
    migration_effort: str            # low | medium | high
```

---

## Open Questions for Brainstorm Session

**risk_ranker:**
- How many deps can the agent realistically process in one ReAct loop before hitting context limits?
- Should the agent batch-retrieve all signals upfront or retrieve lazily per dep?
- What's the scoring scale — 0–10, 0–100, or categorical?

**risk_score:**
- Should the per-dimension weights be hardcoded, configurable, or learned from the concern context?
- How should `impact_weight` be factored in — additive, multiplicative, or as a separate axis?

**recommendation:**
- How do we handle deps with no real alternative (e.g., `react`, `typescript`)?
- Should the agent verify that the alternative is compatible with the project's current Node/package versions?
- `search_npm` and `get_github_summary` need real implementations — what APIs back them?
