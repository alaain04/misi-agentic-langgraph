# API Reference

Base URL: `http://localhost:8000`

---

## POST /analyze

Submit a dependency analysis job. Returns immediately with a `trace_id` to poll for results.

**Request body**

```json
{
  "repo_url": "https://github.com/owner/repo",
  "concern": "Are there vulnerable dependencies in this project?"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `repo_url` | `string` | yes | GitHub repository URL to analyse |
| `concern` | `string` | yes | What risk to analyse (natural language) |

**Response — 202 Accepted**

```json
{
  "trace_id": "682a1f3c4e5d6a7b8c9d0e1f",
  "status": "pending"
}
```

---

## GET /analyze/{trace_id}

Poll the status of a previously submitted job.

**Path parameters**

| Param | Type | Description |
|---|---|---|
| `trace_id` | `string` | The `trace_id` returned by `POST /analyze` |

**Response — 200 OK**

```json
{
  "trace_id": "682a1f3c4e5d6a7b8c9d0e1f",
  "status": "running",
  "metadata": {
    "repo_url": "https://github.com/owner/repo",
    "concern": "Are there vulnerable dependencies?"
  },
  "completed_at": null,
  "results": null,
  "artifacts": [
    {
      "node": "discovery",
      "status": "done",
      "started_at": "2026-04-25T12:00:01Z",
      "completed_at": "2026-04-25T12:00:03Z"
    }
  ],
  "graph": {
    "nodes": [
      { "id": "discovery", "type": "backbone", "order": 0 }
    ],
    "edges": [
      { "source": "discovery", "target": "orchestrator" }
    ]
  }
}
```

| Field | Type | Description |
|---|---|---|
| `trace_id` | `string` | Job identifier |
| `status` | `JobStatus` | Current job status |
| `metadata` | `object` | Original request fields (`repo_url`, `concern`) |
| `completed_at` | `string (ISO 8601) \| null` | Timestamp of completion; present when `done` or `failed` |
| `results` | `object \| null` | Analysis result; present only when `done` |
| `artifacts` | `Artifact[]` | Per-node execution entries; empty until the job starts |
| `graph` | `GraphInfo` | Node/edge structure for rendering the execution DAG |

**Artifact fields**

| Field | Type | Description |
|---|---|---|
| `node` | `string` | Graph node name (e.g. `"discovery"`, `"orchestrator"`) |
| `status` | `"running" \| "done" \| "failed"` | Current execution status |
| `started_at` | `string (ISO 8601)` | When the node started |
| `completed_at` | `string (ISO 8601) \| null` | When the node finished; null while running |

**GraphNodeInfo fields**

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Node identifier |
| `type` | `"terminal" \| "backbone" \| "subgraph"` | Node role in the pipeline |
| `order` | `number` | Execution order index |

**Response — 404 Not Found**

```json
{ "detail": "trace_id not found" }
```

---

## POST /analyze/{trace_id}/chat

Send a message to a job that is in `awaiting_approval` status. Resumes the paused LangGraph execution in the background.

**Path parameters**

| Param | Type | Description |
|---|---|---|
| `trace_id` | `string` | The `trace_id` returned by `POST /analyze` |

**Request body**

```json
{
  "message": "Yes, proceed with the full analysis."
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `message` | `string` | yes | Natural language response to the orchestrator's plan proposal |

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
{ "detail": "Job is not awaiting user input (status: done)" }
```

---

## GET /jobs

List all analysis jobs with pagination and optional filters, sorted by creation time descending.

**Query parameters**

| Param | Type | Default | Description |
|---|---|---|---|
| `page` | `integer` | `1` | Page number (≥ 1) |
| `limit` | `integer` | `10` | Items per page (1–100) |
| `status` | `JobStatus` | — | Filter by status |
| `trace_id` | `string` | — | Filter by exact trace_id |

**Response — 200 OK**

```json
{
  "items": [
    {
      "trace_id": "682a1f3c4e5d6a7b8c9d0e1f",
      "status": "done",
      "concern": "Are there vulnerable dependencies?",
      "created_at": "2026-04-25T11:00:00Z",
      "completed_at": "2026-04-25T11:00:10Z"
    }
  ],
  "total": 42,
  "page": 1,
  "limit": 10,
  "pages": 5
}
```

**JobListItem fields**

| Field | Type | Description |
|---|---|---|
| `trace_id` | `string` | Job identifier |
| `status` | `JobStatus` | Current job status |
| `concern` | `string` | The original user concern |
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
| `awaiting_approval` | Orchestrator has proposed a plan; waiting for user response via `POST /analyze/{trace_id}/chat` |
| `done` | Analysis completed successfully |
| `failed` | Pipeline encountered an unrecoverable error |

---

## TypeScript types

```ts
type JobStatus = "pending" | "running" | "awaiting_approval" | "done" | "failed";

interface AnalyzeRequest {
  repo_url: string;
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

interface GraphNodeInfo {
  id: string;
  type: "terminal" | "backbone" | "subgraph";
  order: number;
}

interface GraphEdgeInfo {
  source: string;
  target: string;
}

interface GraphInfo {
  nodes: GraphNodeInfo[];
  edges: GraphEdgeInfo[];
}

interface JobMetadata {
  repo_url: string;
  concern: string;
}

interface StatusResponse {
  trace_id: string;
  status: JobStatus;
  metadata: JobMetadata;
  completed_at: string | null;
  results: Record<string, unknown> | null;
  artifacts: Artifact[];
  graph: GraphInfo;
}

interface ChatRequest {
  message: string;
}

interface ChatResponse {
  trace_id: string;
  status: "running";
}

interface JobListItem {
  trace_id: string;
  status: JobStatus;
  concern: string;
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

interface ErrorResponse {
  detail: string;
}
```
