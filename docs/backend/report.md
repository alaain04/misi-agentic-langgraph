# Analysis Report

The final output of the pipeline. Available in two places:
- `GET /analyze/{trace_id}` → `results.analysis_report` (when `status === "done"`)
- `GET /analyze/{trace_id}` → `artifacts` → `report_builder.output`

---

## AnalysisReport

```typescript
interface AnalysisReport {
  concern: string;                    // the original user concern
  generated_at: string;              // ISO 8601
  overall_risk_level: "critical" | "high" | "medium" | "low" | "none";
  summary: {
    total_deps: number;              // total dependencies with findings
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
  findings: ReportFinding[];         // sorted by risk_score descending
  recommendations: string[];         // deduplicated, ordered by finding risk_score
  contradictions: {
    description: string;
    resolution: string;              // "effective_risk_reduced"|"unresolved"|"context_dependent"
  }[];
}
```

---

## ReportFinding

One entry per dependency with findings. Sorted by `risk_score` descending.

```typescript
interface ReportFinding {
  dep_name: string;
  risk_score: number;                 // 0–10
  confidence: number;                 // 0–1
  severity: "critical" | "high" | "medium" | "low" | "info";
  summary: string;                    // human-readable risk summary
  recommendation: string | null;
  alternatives: string[];             // suggested replacement packages
  supporting_evidence_count: number;
  contradictions_count: number;
  missing_evidence: string[];         // what would improve confidence
}
```

---

## BlastRadiusSummary

Populated on `ReportFinding.blast_radius` when the per-finding enrichment
agent's `impact_analysis` tool ran. `null` only if enrichment itself never
completed (e.g. total LLM outage for that finding).

```typescript
interface BlastRadiusSummary {
  available: boolean;                 // false only if neither codegraph nor semantic search found anything
  affected_file_count: number;
  affected_files: string[];           // "path:line" entries
  production_file_count: number;
  isolated_to_tests_or_scripts: boolean;
  node_count: number;                 // 0 when source is not "codegraph"
  depth_searched: number;             // 0 when source is not "codegraph"
  use_cases_impacted: string[];       // business capabilities the affected code implements
  narrative: string;                  // 1-3 sentence real-world impact summary
  source: "codegraph" | "semantic_search" | "unavailable";
}
```

Note: `ReportFinding` above (`risk_score`, `confidence`, `summary`,
`supporting_evidence_count`, `contradictions_count`, `missing_evidence`)
predates the per-finding-agent refactor and no longer matches
`src/models/results.py`'s current `ReportFinding` shape (which also has
`business_impact`, `evidence`, `trust`, `observation`, `blast_radius`).
Full resync of that section is out of scope for this change — flagged here
for a follow-up doc pass.

---

## RiskFinding (raw, in results.risk_findings)

The raw finding objects in `results.risk_findings` carry more detail than the report:

```typescript
interface RiskFinding {
  dep_name: string;
  risk_score: number;           // 0–10
  confidence: number;           // 0–1
  severity: Severity;
  hypotheses: Hypothesis[];     // see hitl.md
  supporting_evidence: string[];           // evidence IDs
  contradictions: ContradictionReport[];
  missing_evidence: string[];
  summary: string;
  recommendation: string | null;
  alternatives: string[];
}

interface ContradictionReport {
  evidence_ids: string[];
  description: string;
  resolution: string;           // "effective_risk_reduced"|"unresolved"|"context_dependent"
  adjusted_confidence: number;  // 0–1
}
```

---

## Severity levels

| value | meaning |
|-------|---------|
| `critical` | Immediate action required |
| `high` | High risk, prioritize remediation |
| `medium` | Moderate risk, plan remediation |
| `low` | Low risk, monitor |
| `info` | Informational, no action required |

`overall_risk_level` is the highest severity across all findings. `none` when no findings.

---

## Available investigation skills

The planner assigns skills from this registry. Each skill produces one or more `Evidence` items:

| skill_id | description |
|----------|-------------|
| `vulnerability` | Known CVEs from SBOM via Trivy/OSV |
| `maintainer_trust` | Maintainer activity, bus factor, contributor signals |
| `supply_chain` | Typosquatting, provenance, install scripts |
| `license` | License conflicts, copyleft, compatibility |
| `reachability` | Whether vulnerable code is actually reachable |
| `blast_radius` | How many modules depend on this package |
| `release_anomaly` | Unusual release patterns, version gaps |
| `ecosystem` | Package health, downloads, community signals |
