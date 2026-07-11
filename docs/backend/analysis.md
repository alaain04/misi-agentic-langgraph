# Analysis Endpoints

---

## POST /analyze

Start a new analysis job. Returns immediately (202 Accepted); the graph runs in the background.

**Request**
```json
{
  "repo_url": "https://github.com/org/repo",
  "concern": "Check for outdated dependencies with known CVEs"
}
```

**Response 202**
```json
{
  "trace_id": "68694b3f9b...",
  "status": "pending"
}
```

Store `trace_id` and poll `GET /analyze/{trace_id}` to track progress.

---

## GET /analyze/{trace_id}

Poll job status. Returns the full `AnalysisStatusResponse`.

**Response 200**
```typescript
interface AnalysisStatusResponse {
  trace_id: string;
  status: JobStatus;              // "pending"|"running"|"processing"|"awaiting_approval"|"done"|"failed"|"cancelled"
  metadata: {
    repo_url: string;
    concern: string;
  };
  completed_at: string | null;    // ISO 8601
  results: JobResult | null;      // populated on status==="done"
  artifacts: Artifact[];          // live execution tracking — see artifacts.md
  graph: GraphInfo;               // static DAG topology
}
```

**Response 404** — `trace_id` not found.

---

### GraphInfo

Describes the static pipeline DAG (same shape for every job).

```typescript
interface GraphInfo {
  nodes: GraphNodeInfo[];
  edges: GraphEdgeInfo[];
}

interface GraphNodeInfo {
  id: string;                     // node name or "START"/"END"
  type: "terminal" | "backbone" | "subgraph";
  order: number;                  // 0=START, 1-8=backbone, 9=END
}

interface GraphEdgeInfo {
  source: string;
  target: string;
}
```

Node order and IDs:

| order | id | type |
|-------|----|------|
| 0 | START | terminal |
| 1 | discovery | backbone |
| 2 | investigation_planner | backbone |
| 3 | skill_dispatcher | backbone |
| 4 | skill_executor | backbone |
| 5 | evidence_collector | backbone |
| 6 | evidence_correlator | backbone |
| 7 | finding_reviewer | backbone |
| 8 | report_builder | backbone |
| 9 | END | terminal |

---

### JobResult

The `results` field, only present when `status === "done"`.

```typescript
interface JobResult {
  discovery: {
    project_metadata: ProjectMetadata | null;
    manifest_files: string[] | null;
    discovery_summary: string | null;
    discovery_error: string | null;
    sbom_result_id: string | null;
    sbom_error: string | null;
    lock_generation_error: string | null;
  };
  risk_findings: RiskFinding[];      // raw findings array — see report.md for processed form
  analysis_report: AnalysisReport;   // see report.md
  review_approved: boolean | null;
  review_iterations: number | null;
}

interface ProjectMetadata {
  name: string;
  package_manager: string;
  direct_dependencies_count: number;
  transitive_dependencies_count: number;
}
```

See [report.md](report.md) for the full `AnalysisReport` and `RiskFinding` shapes.

---

### Polling strategy

```
poll GET /analyze/{trace_id} every 2–3 seconds

status === "running" | "processing"  →  keep polling, render artifacts live
status === "awaiting_approval"       →  stop polling, show HITL chat UI (see hitl.md)
status === "done"                    →  stop polling, render results.analysis_report
status === "failed" | "cancelled"    →  stop polling, show error/cancelled state
```
