# Jobs Endpoint

---

## GET /jobs

Paginated list of all jobs, sorted by `created_at` descending.

**Query parameters**

| param | type | default | description |
|-------|------|---------|-------------|
| `page` | int ≥ 1 | 1 | Page number |
| `limit` | int 1–100 | 10 | Items per page |
| `status` | JobStatus | — | Filter by status |
| `trace_id` | string | — | Partial match on trace_id (case-insensitive) |

**Response 200**

```typescript
interface JobsListResponse {
  items: JobListItem[];
  total: number;
  page: number;
  limit: number;
  pages: number;   // ceil(total / limit)
}

interface JobListItem {
  trace_id: string;
  status: JobStatus;
  concern: string;
  created_at: string;            // ISO 8601
  completed_at: string | null;   // ISO 8601
}
```

---

## JobStatus enum

```typescript
type JobStatus =
  | "pending"            // created, not yet started
  | "running"            // graph is executing
  | "processing"         // resuming after HITL gate
  | "awaiting_approval"  // paused at a HITL gate, needs /chat input
  | "done"               // completed successfully
  | "failed"             // graph error
  | "cancelled";         // user sent cancel intent at investigation_planner gate
```

**Terminal states:** `done`, `failed`, `cancelled` — stop polling when reached.

**Active states:** `running`, `processing` — graph is executing, artifacts are updating.

**Interaction required:** `awaiting_approval` — show HITL chat UI.
