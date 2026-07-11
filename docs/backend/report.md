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
