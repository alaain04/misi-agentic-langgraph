# Backend API Reference

Base URL: `http://localhost:8000`

## Domains

| Domain | File | Endpoints |
|--------|------|-----------|
| [Analysis](backend/analysis.md) | Start and poll an analysis job | `POST /analyze`, `GET /analyze/{trace_id}` |
| [HITL](backend/hitl.md) | Human-in-the-loop chat gates | `POST /analyze/{trace_id}/chat` |
| [Jobs](backend/jobs.md) | List all jobs | `GET /jobs` |
| [Report](backend/report.md) | Final report shape (inside status response) | — |
| [Artifacts](backend/artifacts.md) | Per-node execution artifacts (inside status response) | — |

---

## Quick endpoint reference

```
POST   /analyze                        Start a new analysis
GET    /analyze/{trace_id}             Poll status (artifacts + result)
POST   /analyze/{trace_id}/chat        Send user message at a HITL gate
GET    /jobs?page&limit&status&trace_id  List jobs (paginated)
```

---

## Job status lifecycle

```
pending
  └─ running              graph execution started
       ├─ awaiting_approval    HITL gate active — waiting for /chat
       │    └─ processing      resume in flight
       │         └─ running    resumed, continuing execution
       ├─ done
       ├─ failed
       └─ cancelled            user sent "cancel" intent
```

The two HITL gates that trigger `awaiting_approval`:
1. `investigation_planner` — present proposed investigation plan for approval
2. `finding_reviewer` — present risk findings for user acknowledgement

See [hitl.md](backend/hitl.md) for how to detect which gate is active.
