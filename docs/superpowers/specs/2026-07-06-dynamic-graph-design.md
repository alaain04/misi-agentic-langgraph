# Dynamic Execution Graph from Artifacts

**Date:** 2026-07-06
**Status:** Approved

## Problem

The backend generates a static `GraphInfo` (nodes + edges) and serializes it into every status response. The frontend uses it as a scaffold and overlays artifact data to compute status. The graph is always the same fixed topology regardless of what actually ran. It cannot show per-iteration conductor state, individual tool executions, or the fact that `hitl_gate` only exists in some runs.

The artifacts array already contains everything needed: which nodes ran, in what order, their status, and their data. The `graph` field is redundant.

## Goal

Replace the static graph with a dynamic one derived entirely from artifacts. Show each conductor iteration as its own node, each tool call as its own node, and edges that reflect the actual ReAct loop execution trace.

## Approach

Artifact-first: the frontend mapper becomes the graph builder. The backend stops generating `GraphInfo` entirely. The only backend change is accumulating per-iteration data instead of overwriting.

## Data Model Changes

### ConductorArtifact

Replace flat fields with an `iterations` array (append on each conductor run):

```ts
interface ConductorIteration {
  iteration: number
  tool_calls: ToolCall[]
  findings_count: number
  finalize: boolean
  reasoning: string
}

interface ConductorArtifact extends BaseArtifact {
  node: 'conductor'
  iterations: ConductorIteration[]
}
```

### ToolRunnerArtifact

Replace flat fields with an `iterations` array (append on each tool_runner run):

```ts
interface ToolRunnerIteration {
  conductor_iteration: number
  tools_run: string[]
  errors: ToolError[]
}

interface ToolRunnerArtifact extends BaseArtifact {
  node: 'tool_runner'
  iterations: ToolRunnerIteration[]
}
```

## Backend Changes

### `job_dao.py`

Add `push_artifact_item(job_id, node, field, item)` — appends an item to a named array field inside an artifact using `$push`. Used by conductor and tool_runner handlers.

### `job_runner.py`

- CONDUCTOR handler: call `push_artifact_item(job_id, CONDUCTOR, "iterations", iteration_data)` instead of `update_artifact_data`
- TOOL_RUNNER handler: call `push_artifact_item(job_id, TOOL_RUNNER, "iterations", iteration_data)` instead of `update_artifact_data`

### Deletions

- `apps/backend/src/api/service.py` — deleted entirely
- `apps/backend/src/api/schemas.py` — remove `GraphInfo`, `GraphNodeInfo`, `GraphEdgeInfo`; remove `graph` field from `AnalysisStatusResponse`
- `apps/backend/src/api/routes.py` — remove `build_graph_info` import and call

## Frontend Changes

### `api/types.ts`

- Update `ConductorArtifact` and `ToolRunnerArtifact` to use `iterations` arrays
- Remove `GraphInfo`, `GraphNodeInfo`, `GraphEdgeInfo`
- Remove `graph` from `StatusResponse`

### `graphDefinition.ts`

- Remove `GRAPH_NODES`, `GRAPH_EDGES`, `buildGraphDef`
- `NodeId` becomes `string` (was a discriminated union)
- Keep `GraphNodeDef`, `GraphEdgeDef`, `GraphNodeState`, `GraphRenderData`, `NodeStatus`
- Add `nodeKind(id: string)` helper that parses dynamic IDs:
  - `"conductor:3"` → `{ kind: 'conductor', iter: 3 }`
  - `"tool:npm_audit:2"` → `{ kind: 'tool', name: 'npm_audit', iter: 2 }`
  - `"prep"` → `{ kind: 'prep' }`

### `graphStateMapper.ts` (rewrite)

`mapResponseToGraphState(response)` builds graph entirely from `response.artifacts` and `response.status`.

**Node sequence:**

| Node ID | Condition |
|---|---|
| `START` | Always |
| `prep` | Always (once job exists) |
| `conductor:N` | One per entry in conductor artifact `iterations` |
| `tool:X:N` | One per tool in `tool_runner.iterations` where `conductor_iteration === N` |
| `hitl_gate` | Only if hitl_gate artifact exists |
| `report_builder` | Only if report_builder artifact exists |
| `END` | Only if job is `done` or `failed` |

**Layer assignment** (drives vertical positioning):
- START: 0, prep: 1
- `conductor:N`: layer `2 + (N-1) * 2`
- Tools of iteration N: layer `2 + (N-1) * 2 + 1`, `laneIndex` 0,1,2… per parallel tool
- hitl_gate / report_builder / END: continue incrementing after last iteration

**Edge derivation:**
- `START → prep → conductor:1`
- `conductor:N → tool:X:N` for each tool in iteration N
- `tool:*:N → conductor:N+1` for all tools in iteration N (ReAct loop-back)
- Final conductor (finalize=true) → `hitl_gate` if present, else `report_builder`
- `hitl_gate → conductor:{resume_iter}` (the next conductor iteration after approval)
- Last `conductor → report_builder → END`

**Status derivation per node:**
- `conductor:N`: `active` if it's the latest and job is running; `done` otherwise
- `tool:X:N`: `failed` if in errors list; `done` otherwise
- `hitl_gate`: `awaiting` if `status === 'running'` and has messages; `done` if status is done
- Others: unchanged from current logic

### `nodeRegistry.ts`

Replace exact-ID Map with prefix-based function:

```ts
function getPanelComponent(id: string): PanelComponent | undefined {
  if (id === 'conductor' || id.startsWith('conductor:')) return ConductorPanel
  if (id.startsWith('tool:'))                              return ToolPanel
  if (id === 'hitl_gate')                                  return HitlGatePanel
  if (id === 'report_builder')                             return ReportBuilderPanel
  if (id === 'discovery')                                  return DiscoveryPanel
  // etc.
}
```

### New `ToolPanel`

Parses `tool:npm_audit:2` from the node ID, finds the matching `ToolRunnerIteration` in the artifact, shows: tool name, iteration number, error if any.

### Updated `ConductorPanel`

Receives the node ID (`conductor:N`), extracts iteration N, finds the matching `ConductorIteration` in the artifact, shows: iteration number, reasoning, findings count, tool calls list.

### Visual Labels

- `conductor:3` renders as `conductor 3`
- `tool:npm_audit:2` renders as `npm_audit`

### `ExecutionPage.tsx`

No changes needed — already passes the full response to `mapResponseToGraphState`.
