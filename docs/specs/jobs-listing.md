# Spec: Jobs Listing Page

## Overview

A frontend page at `/jobs` that lists all analysis job executions in a paginated table. Each row links to a job detail page at `/jobs/:traceId`.

---

## Backend

### Data model change

Add `completed_at: datetime | None = None` to the `Job` model (`backend/src/models/job.py`).

This field is set when the job transitions to `done` or `failed` status.

### New endpoint: GET /jobs

Query params:
- `page` (int, default `1`)
- `limit` (int, default `20`)

Response `200 OK`:
```json
{
  "items": [
    {
      "trace_id": "682b1c3e4f5a6b7c8d9e0f1a",
      "status": "done",
      "created_at": "2026-04-25T10:00:00Z",
      "completed_at": "2026-04-25T10:00:45Z"
    }
  ],
  "total": 42,
  "page": 1,
  "limit": 20,
  "pages": 3
}
```

Items are sorted by `created_at` descending (most recent first).

### Updated endpoint: GET /analyze/{trace_id}

Updated response now includes `completed_at` and `result` (when status is `done`):
```json
{
  "trace_id": "...",
  "status": "done",
  "completed_at": "2026-04-25T10:00:45Z",
  "result": { ... }
}
```

---

## Frontend

### Routing

Install `react-router-dom`. Routes added to `App.tsx`:

| Path | Component |
|------|-----------|
| `/` | `AnalysisPage` (existing, unchanged) |
| `/jobs` | `JobsListPage` |
| `/jobs/:traceId` | `JobDetailPage` |

Add a nav link to `/jobs` in `Header.tsx`.

### JobsListPage (`/jobs`)

Paginated table of all jobs, fetched from `GET /jobs`.

**Table columns:**

| Column | Notes |
|--------|-------|
| `#` | Row number within the current page |
| Trace ID | First 8 chars + `…`, monospace font |
| Status | `Badge` component (pending / running / done / failed) |
| Started | Local datetime formatted from `created_at` |
| Processed | Local datetime from `completed_at`, or `—` if null |
| → | `Link` to `/jobs/:traceId` |

**Pagination:**
- Prev / Next buttons (disabled at boundaries)
- "Page N of M" label
- 20 rows per page

**States:**
- Loading: centered `Spinner`
- Error: inline error message
- Empty: "No executions yet." message

### JobDetailPage (`/jobs/:traceId`)

Detail view for a single job, fetched from `GET /analyze/:traceId`.

**Displays:**
- Status `Badge`
- Trace ID (full, monospace)
- Started (`created_at`)
- Processed (`completed_at` or `—`)
- Concern text

**Behavior:**
- While `pending` or `running`: shows `Spinner` and auto-polls every 2 s (reuses `usePolling` hook)
- When `done`: renders existing `AnalysisResult` component
- When `failed`: shows error state

**Navigation:** "← Back to jobs" link at the top.

---

## Design

Follow the existing design system:
- Tailwind CSS + CSS custom properties (`--color-*`, `--color-border`, etc.)
- Reuse `Badge`, `Button`, `Spinner` from `components/ui/`
- No new UI libraries
