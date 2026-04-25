# API Reference

Base URL: `http://localhost:8000`

---

## POST /analyze

Submit a dependency analysis job. Returns immediately with a `trace_id` to poll for results.

**Request body**

```json
{
  "package_json": "<contents of package.json as a string>",
  "lock_file": "<contents of the lock file as a string>",
  "lock_file_name": "package-lock.json" | "yarn.lock" | "pnpm-lock.yaml",
  "concern": "<natural language description of the risk concern>"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `package_json` | `string` | yes | Raw contents of `package.json` |
| `lock_file` | `string` | yes | Raw contents of the lock file |
| `lock_file_name` | `"package-lock.json" \| "yarn.lock" \| "pnpm-lock.yaml"` | yes | Lock file type |
| `concern` | `string` | yes | What risk to analyse (e.g. "security vulnerabilities") |

**Response — 202 Accepted**

```json
{
  "trace_id": "682a1f3c4e5d6a7b8c9d0e1f",
  "status": "pending"
}
```

---

## GET /analyze/{trace_id}

Poll the status of a previously submitted job. When the job is done, `result` is included. When the job has finished (done or failed), `completed_at` is included.

**Path parameters**

| Param | Type | Description |
|---|---|---|
| `trace_id` | `string` | The `trace_id` returned by `POST /analyze` |

**Response — 200 OK**

```json
{
  "trace_id": "682a1f3c4e5d6a7b8c9d0e1f",
  "status": "pending" | "running" | "awaiting_approval" | "done" | "failed",
  "completed_at": "2026-04-25T12:00:00Z" | null,
  "result": { ... } | null,
  "artifacts": [
    {
      "node": "project_discovery",
      "status": "running" | "done" | "failed",
      "started_at": "2026-04-25T12:00:01Z",
      "completed_at": "2026-04-25T12:00:03Z" | null
    }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `trace_id` | `string` | Job identifier |
| `status` | `JobStatus` | Current job status |
| `completed_at` | `string (ISO 8601) \| null` | Timestamp of completion; present when status is `done` or `failed` |
| `result` | `object \| null` | Analysis result; present only when status is `done` |
| `artifacts` | `Artifact[]` | Per-node execution status entries; empty until the job starts running |

**Artifact fields**

| Field | Type | Description |
|---|---|---|
| `node` | `string` | Graph node or subgraph name (e.g. `"project_discovery"`, `"registry"`) |
| `status` | `"running" \| "done" \| "failed"` | Current execution status of this node |
| `started_at` | `string (ISO 8601)` | When the node started executing |
| `completed_at` | `string (ISO 8601) \| null` | When the node finished; null while still running |

**Response — 404 Not Found**

```json
{
  "detail": "trace_id not found"
}
```

---

## POST /analyze/{trace_id}/approve

Submit a plan approval decision for a job that is `awaiting_approval`. Resumes the paused LangGraph execution in the background.

**Path parameters**

| Param | Type | Description |
|---|---|---|
| `trace_id` | `string` | The `trace_id` returned by `POST /analyze` |

**Request body**

```json
{
  "action": "approve" | "modify" | "cancel" | "refine",
  "plan": ["registry", "risk_score", "recommendation"],
  "feedback": "Please also include the repo subgraph."
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | `"approve" \| "modify" \| "cancel" \| "refine"` | yes | What to do with the generated plan |
| `plan` | `string[]` | no | Replacement subgraph list; required when `action` is `"modify"`, ignored otherwise |
| `feedback` | `string` | no | Natural language feedback used to refine the plan via LLM; required when `action` is `"refine"`, ignored otherwise. The job remains in `awaiting_approval` after a `refine` action. |

**Response — 202 Accepted**

```json
{
  "trace_id": "682a1f3c4e5d6a7b8c9d0e1f",
  "status": "running"
}
```

**Response — 404 Not Found**

```json
{ "detail": "trace_id not found" }
```

**Response — 409 Conflict**

Returned when the job is not currently in `awaiting_approval` status.

```json
{ "detail": "Job is not awaiting approval (status: done)" }
```

---

## GET /jobs

List all analysis jobs with pagination, sorted by creation time descending.

**Query parameters**

| Param | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `page` | `integer` | `1` | `>= 1` | Page number |
| `limit` | `integer` | `20` | `1–100` | Items per page |

**Response — 200 OK**

```json
{
  "items": [
    {
      "trace_id": "682a1f3c4e5d6a7b8c9d0e1f",
      "status": "done",
      "created_at": "2026-04-25T11:00:00Z",
      "completed_at": "2026-04-25T11:00:10Z"
    }
  ],
  "total": 42,
  "page": 1,
  "limit": 20,
  "pages": 3
}
```

| Field | Type | Description |
|---|---|---|
| `items` | `JobListItem[]` | Page of jobs |
| `total` | `integer` | Total number of jobs in the collection |
| `page` | `integer` | Current page |
| `limit` | `integer` | Items per page requested |
| `pages` | `integer` | Total number of pages |

**JobListItem fields**

| Field | Type | Description |
|---|---|---|
| `trace_id` | `string` | Job identifier |
| `status` | `JobStatus` | Current job status |
| `created_at` | `string (ISO 8601)` | When the job was created |
| `completed_at` | `string (ISO 8601) \| null` | When the job finished; null if still running |

---

## Job status lifecycle

```
pending → running → awaiting_approval → running → done
                                                 ↘ failed
                  ↘ done
                  ↘ failed
```

| Status | Meaning |
|---|---|
| `pending` | Job created, not yet started |
| `running` | LangGraph pipeline is executing |
| `awaiting_approval` | Planner has produced a plan; waiting for human approval via `POST /analyze/{trace_id}/approve` |
| `done` | Analysis completed successfully |
| `failed` | Pipeline encountered an unrecoverable error |

---

## TypeScript types

```ts
type LockFileName = "package-lock.json" | "yarn.lock" | "pnpm-lock.yaml";

type JobStatus = "pending" | "running" | "awaiting_approval" | "done" | "failed";

interface AnalyzeRequest {
  package_json: string;
  lock_file: string;
  lock_file_name: LockFileName;
  concern: string;
}

interface AnalyzeResponse {
  trace_id: string;
  status: JobStatus;
}

interface Artifact {
  node: string;
  status: "running" | "done" | "failed";
  started_at: string;
  completed_at: string | null;
}

interface StatusResponse {
  trace_id: string;
  status: JobStatus;
  completed_at: string | null;
  result: Record<string, unknown> | null;
  artifacts: Artifact[];
}

interface JobListItem {
  trace_id: string;
  status: JobStatus;
  created_at: string;
  completed_at: string | null;
}

interface JobsListResponse {
  items: JobListItem[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

interface PlanApprovalRequest {
  action: "approve" | "modify" | "cancel" | "refine";
  plan?: string[];
  feedback?: string;
}

interface PlanApprovalResponse {
  trace_id: string;
  status: "running";
}

interface ErrorResponse {
  detail: string;
}
```
