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
  "status": "pending" | "running" | "done" | "failed",
  "completed_at": "2026-04-25T12:00:00Z" | null,
  "result": { ... } | null
}
```

| Field | Type | Description |
|---|---|---|
| `trace_id` | `string` | Job identifier |
| `status` | `JobStatus` | Current job status |
| `completed_at` | `string (ISO 8601) \| null` | Timestamp of completion; present when status is `done` or `failed` |
| `result` | `object \| null` | Analysis result; present only when status is `done` |

**Response — 404 Not Found**

```json
{
  "detail": "trace_id not found"
}
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
pending → running → done
                  ↘ failed
```

| Status | Meaning |
|---|---|
| `pending` | Job created, not yet started |
| `running` | LangGraph pipeline is executing |
| `done` | Analysis completed successfully |
| `failed` | Pipeline encountered an unrecoverable error |

---

## TypeScript types

```ts
type LockFileName = "package-lock.json" | "yarn.lock" | "pnpm-lock.yaml";

type JobStatus = "pending" | "running" | "done" | "failed";

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

interface StatusResponse {
  trace_id: string;
  status: JobStatus;
  completed_at: string | null;
  result: Record<string, unknown> | null;
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

interface ErrorResponse {
  detail: string;
}
```
