# Artifact Tracking

Every backbone node in the pipeline writes execution data to `job.artifacts` in MongoDB. The `GET /analyze/{trace_id}` response includes the current `artifacts` array, which the frontend uses to render the live execution DAG.

---

## Base artifact shape

All artifacts share this base:

```typescript
interface Artifact {
  node: string;                    // node ID (matches GraphNodeInfo.id)
  status: "running" | "done" | "failed" | "cancelled";
  started_at: string;              // ISO 8601
  completed_at: string | null;     // ISO 8601, null while running
  // node-specific fields below
}
```

---

## Per-node artifact shapes

### discovery

```typescript
{
  node: "discovery",
  status: "running" | "done" | "failed",
  started_at: string,
  completed_at: string | null,
  steps: string[],    // discovery pipeline steps as they complete, e.g.
                      // ["clone_repository", "inspect_repo", "generate_sbom", "build_dependency_summary"]
}
```

`status: "failed"` when `discovery_error` or `sbom_error` is set.

---

### investigation_planner

```typescript
{
  node: "investigation_planner",
  status: "running" | "done",
  started_at: string,
  completed_at: string | null,
  data: {
    plan: {
      rationale: string;
      hypotheses: Hypothesis[];
      dep_filter: string[] | null;
    }
  },
  messages: ArtifactMessage[],   // HITL chat history
}

interface ArtifactMessage {
  role: "assistant" | "human";
  content: string;
  created_at: string;            // ISO 8601
  action?: "approve" | "change" | "cancel";  // human messages only
}
```

Populated during Gate 1 HITL. `status: "done"` after user approves.

---

### evidence_collector

```typescript
{
  node: "evidence_collector",
  status: "running" | "done",
  started_at: string,
  completed_at: string | null,
  steps: string[],    // list of executed skill tasks, e.g.
                      // ["vulnerability:lodash", "supply_chain:axios"]
}
```

---

### evidence_correlator

```typescript
{
  node: "evidence_correlator",
  status: "running" | "done",
  started_at: string,
  completed_at: string | null,
  data: {
    findings_count: number,
    contradictions_count: number,
    deps_covered: string[],    // dep names with findings
  }
}
```

---

### finding_reviewer

```typescript
{
  node: "finding_reviewer",
  status: "running" | "done",
  started_at: string,
  completed_at: string | null,
  data: {
    risk_findings: RiskFinding[],    // findings requiring review (see report.md)
  },
  output: {
    review_approved: boolean,
    reviewer_feedback: string | null,    // null when approved on first pass
  },
  messages: ArtifactMessage[],         // Gate 2 HITL chat history (same shape as investigation_planner)
}
```

---

### report_builder

```typescript
{
  node: "report_builder",
  status: "running" | "done",
  started_at: string,
  completed_at: string | null,
  output: AnalysisReport,    // see report.md
}
```

---

## Node execution order

The pipeline runs nodes in this order. Use `artifact.status` and the node's `order` from `GraphInfo` to render progress:

```
discovery → investigation_planner → [skill fan-out] → evidence_collector
  → evidence_correlator → finding_reviewer → report_builder
```

`skill_dispatcher` and `skill_executor` have no artifacts (dispatcher is a routing function; executor instances are ephemeral). Their progress is visible via `evidence_collector.steps`.
