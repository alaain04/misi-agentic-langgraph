# HITL Node Chat History

## Problem

Two issues block clients from interacting correctly with HITL gates:

1. When polling `GET /analyze/{trace_id}`, the client cannot identify which HITL gate is waiting for input.
2. The client cannot read the plan (gate 1) or the risk findings (gate 2) needed to make an informed approval decision.

### Root causes

- `push_proposal` in `JobDAO` hardcodes `"orchestrator"` as the artifact node name. Since the refactor renamed that node to `"investigation_planner"`, every proposal write silently fails (logs a warning, no data saved).
- `finding_reviewer` has no proposal storage at all — it calls `interrupt()` but nothing is persisted to MongoDB.
- The interrupt payload is captured in `job_runner.py` but discarded.

---

## Design

### Artifact shape for HITL nodes

Each HITL node artifact gains two new fields alongside the existing `node`, `status`, `started_at`, `completed_at`:

**`messages: list[dict]`** — append-only ordered thread of assistant/human turns.

Each entry:
```json
{ "role": "assistant", "content": "...", "created_at": "..." }
{ "role": "human",    "content": "...", "created_at": "...", "action": "approve|change|cancel" }
```

`action` is only present on human messages. It carries the classified intent (gate 1) or always `"approve"` (gate 2, where any response is an acknowledgment).

**`data: dict`** — latest structured payload for the node. Overwritten on each re-plan round.

- `investigation_planner`: `{ "plan": { "hypotheses": [...], "rationale": "..." } }`
- `finding_reviewer`: `{ "risk_findings": [...] }`

#### Example — gate 1 with one change round

```json
{
  "node": "investigation_planner",
  "status": "done",
  "started_at": "...",
  "completed_at": "...",
  "messages": [
    { "role": "assistant", "content": "Proposed plan...", "created_at": "..." },
    { "role": "human",    "content": "focus more on licenses", "created_at": "...", "action": "change" },
    { "role": "assistant", "content": "Updated plan...", "created_at": "..." },
    { "role": "human",    "content": "ok proceed", "created_at": "...", "action": "approve" }
  ],
  "data": { "plan": { "hypotheses": [...], "rationale": "..." } }
}
```

#### Example — gate 2

```json
{
  "node": "finding_reviewer",
  "status": "done",
  "started_at": "...",
  "completed_at": "...",
  "messages": [
    { "role": "assistant", "content": "High-severity findings require review...", "created_at": "..." },
    { "role": "human",    "content": "acknowledged", "created_at": "...", "action": "approve" }
  ],
  "data": { "risk_findings": [...] }
}
```

---

### Inferring which node is awaiting input

No new field is added to the API response. When `job.status == "awaiting_approval"`, the node currently paused is the one whose artifact has `status == "running"`. All nodes before it will be `"done"`; nodes after it will not be in the artifacts list yet.

The client derives the awaiting node: `status == "awaiting_approval"` → find artifact with `status == "running"`.

---

### API response

`GET /analyze/{trace_id}` response is unchanged in schema. The HITL artifact data is accessible through the existing `artifacts` array.

---

## Changes

### `src/services/job_dao.py` + `src/domain/ports/job_repository_port.py`

Remove `push_proposal` and `update_proposal`.

Add:

```python
async def push_artifact_message(self, job_id: str, node: str, message: dict) -> None
```

Appends `message` to `artifacts[node].messages` via `$push`. If the artifact does not exist yet, upserts it with `status: "running"` and `messages: [message]`. This handles `finding_reviewer`, which calls this method before the runner has created its artifact.

`update_artifact_data` already exists and handles setting `data` — no changes needed.

### `src/main_graph/nodes/investigation_planner_service.py`

Replace `push_proposal` + `update_proposal` calls:

- Before `interrupt()`: call `push_artifact_message` with `role: "assistant"` message, then `update_artifact_data` with the current plan under `data.plan`.
- After `interrupt()` returns `user_input`: call `push_artifact_message` with `role: "human"` message including `action: intent`.
- On re-plan loop: call `update_artifact_data` again to overwrite `data.plan` with the new plan before pushing the next assistant message.

### `src/main_graph/nodes/finding_reviewer.py`

Update node signature to accept `RunnableConfig` (same pattern as `investigation_planner.py`). Inject `dao` via `get_services(config)` and read `job_id` from state.

Before `interrupt()`:
- `push_artifact_message` with `role: "assistant"` message
- `update_artifact_data` with `data.risk_findings`

After `interrupt()` returns `user_input`:
- `push_artifact_message` with `role: "human"`, `action: "approve"`

### Tests

Update any unit tests that mock `push_proposal` or `update_proposal` on the DAO to use `push_artifact_message` instead.
