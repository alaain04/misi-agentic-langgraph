# Dynamic Execution Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static hardcoded `graph` API field with a dynamic execution graph built entirely from per-iteration artifact data.

**Architecture:** The backend accumulates one record per conductor/tool_runner iteration (instead of overwriting). The frontend `graphStateMapper` derives graph topology — nodes, edges, layers — purely from artifacts. The static `graph` field is deleted from the API.

**Tech Stack:** Python/FastAPI (backend), TypeScript/React/Vite (frontend), MongoDB `$push` for accumulation, D3 layout unchanged.

## Global Constraints

- Python: use `uv run pytest` to run tests; `uv run ruff check .` for lint
- TypeScript: `pnpm run build` (runs `tsc -b && vite build`) to type-check
- Working directories: `apps/backend/` for Python, `apps/frontend/` for TypeScript
- No new dependencies

---

### Task 1: Backend — accumulate per-iteration artifacts

**Files:**
- Modify: `apps/backend/src/domain/ports/job_repository_port.py`
- Modify: `apps/backend/src/services/job_dao.py`
- Modify: `apps/backend/src/services/job_runner.py`
- Test: `apps/backend/tests/unit/test_job.py`

**Interfaces:**
- Produces: `push_artifact_item(job_id, node, field, item)` on port and DAO
- Produces: conductor artifact with `iterations: list[dict]` (each dict has `iteration`, `tool_calls`, `findings_count`, `finalize`, `reasoning`, `started_at`)
- Produces: tool_runner artifact with `iterations: list[dict]` (each dict has `conductor_iteration`, `tools_run`, `errors`, `started_at`)

- [ ] **Step 1: Write the failing test**

Add to `apps/backend/tests/unit/test_job.py`:

```python
def test_job_dao_implements_push_artifact_item():
    from src.domain.ports.job_repository_port import JobRepositoryPort
    from src.services.job_dao import JobDAO
    import inspect

    assert hasattr(JobDAO, "push_artifact_item")
    assert inspect.iscoroutinefunction(JobDAO.push_artifact_item)
    # Port must declare it too
    assert "push_artifact_item" in {m for m in dir(JobRepositoryPort) if not m.startswith("_")}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_job.py::test_job_dao_implements_push_artifact_item -v
```

Expected: `FAILED — AttributeError: push_artifact_item`

- [ ] **Step 3: Add abstract method to port**

In `apps/backend/src/domain/ports/job_repository_port.py`, add after `push_artifact_message`:

```python
    @abstractmethod
    async def push_artifact_item(self, job_id: str, node: str, field: str, item: dict) -> None: ...
```

- [ ] **Step 4: Implement in DAO**

In `apps/backend/src/services/job_dao.py`, add after `push_artifact_message`:

```python
    async def push_artifact_item(self, job_id: str, node: str, field: str, item: dict) -> None:
        """Append an item to an array field inside an existing artifact entry."""
        await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {"$push": {f"artifacts.$.{field}": item}},
        )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd apps/backend && uv run pytest tests/unit/test_job.py::test_job_dao_implements_push_artifact_item -v
```

Expected: `PASSED`

- [ ] **Step 6: Update job_runner to accumulate iterations**

Replace the CONDUCTOR and TOOL_RUNNER handlers in `apps/backend/src/services/job_runner.py`.

First, add `current_conductor_iteration = 0` as a local variable at the top of `_stream_graph`, just before the `async for` loop:

```python
async def _stream_graph(graph, input_data, config, dao: JobRepositoryPort, job_id: str) -> bool:
    """Stream graph updates and track artifacts. Returns True if interrupted."""
    interrupted = False
    current_conductor_iteration = 0
```

Then replace the CONDUCTOR handler block (currently `elif node_name == CONDUCTOR: ...`):

```python
            elif node_name == CONDUCTOR:
                current_conductor_iteration = node_update.get("conductor_iteration") or 0
                decision = node_update.get("conductor_decision")
                if decision:
                    await dao.push_artifact_item(job_id, CONDUCTOR, "iterations", {
                        "iteration": current_conductor_iteration,
                        "tool_calls": [tc.model_dump() for tc in decision.tool_calls],
                        "findings_count": len(node_update.get("findings") or []),
                        "finalize": decision.finalize,
                        "reasoning": decision.reasoning,
                        "started_at": datetime.now(UTC).isoformat(),
                    })
```

Then replace the TOOL_RUNNER handler block (currently `elif node_name == TOOL_RUNNER: ...`):

```python
            elif node_name == TOOL_RUNNER:
                await dao.start_artifact(job_id, TOOL_RUNNER)
                results = node_update.get("tool_results") or []
                await dao.push_artifact_item(job_id, TOOL_RUNNER, "iterations", {
                    "conductor_iteration": current_conductor_iteration,
                    "tools_run": [tr.tool for tr in results],
                    "errors": [{"tool": tr.tool, "error": tr.error} for tr in results if tr.error],
                    "started_at": datetime.now(UTC).isoformat(),
                })
```

- [ ] **Step 7: Run full test suite**

```bash
cd apps/backend && uv run pytest -v
```

Expected: all tests pass (runner changes only affect live graph execution, not unit tests).

- [ ] **Step 8: Commit**

```bash
git add apps/backend/src/domain/ports/job_repository_port.py \
        apps/backend/src/services/job_dao.py \
        apps/backend/src/services/job_runner.py \
        apps/backend/tests/unit/test_job.py
git commit -m "feat(backend): accumulate per-iteration conductor and tool_runner artifacts"
```

---

### Task 2: Backend — remove static graph API

**Files:**
- Delete: `apps/backend/src/api/service.py`
- Modify: `apps/backend/src/api/schemas.py`
- Modify: `apps/backend/src/api/routes.py`

**Interfaces:**
- Consumes: nothing from Task 1
- Produces: `AnalysisStatusResponse` without `graph` field; `GraphNodeInfo`/`GraphEdgeInfo`/`GraphInfo` schemas gone

- [ ] **Step 1: Delete service.py**

```bash
rm apps/backend/src/api/service.py
```

- [ ] **Step 2: Remove graph schemas from schemas.py**

Open `apps/backend/src/api/schemas.py`. Remove these three classes entirely:

```python
class GraphNodeInfo(BaseModel):
    id: str
    type: Literal["terminal", "backbone", "subgraph"]
    order: int


class GraphEdgeInfo(BaseModel):
    source: str
    target: str


class GraphInfo(BaseModel):
    nodes: list[GraphNodeInfo]
    edges: list[GraphEdgeInfo]
```

Also remove the `graph` field from `AnalysisStatusResponse`. The class becomes:

```python
class AnalysisStatusResponse(BaseModel):
    trace_id: str
    status: JobStatus
    metadata: JobMetadata
    completed_at: datetime | None = None
    results: dict | None = None
    error: str | None = None
    artifacts: list[dict] = []
    cost: float | None = None
```

Remove `Literal` from the imports at the top if it's only used by `GraphNodeInfo` — check and clean up:

```python
from pydantic import BaseModel

from src.models.job import JobMetadata, JobStatus
```

- [ ] **Step 3: Clean up routes.py**

Open `apps/backend/src/api/routes.py`. Remove this import:

```python
from src.api.service import build_graph_info
```

Replace the `get_analysis_status` return with (remove the `graph=build_graph_info(job),` line):

```python
    return AnalysisStatusResponse(
        trace_id=job.id,
        status=job.status,
        metadata=job.metadata,
        completed_at=job.completed_at,
        results=job.result,
        error=job.error,
        artifacts=job.artifacts,
        cost=job.cost,
    )
```

- [ ] **Step 4: Run tests**

```bash
cd apps/backend && uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/api/schemas.py apps/backend/src/api/routes.py
git rm apps/backend/src/api/service.py
git commit -m "feat(backend): remove static graph API field"
```

---

### Task 3: Frontend types — update artifact models

**Files:**
- Modify: `apps/frontend/src/api/types.ts`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `ConductorIteration { iteration, tool_calls, findings_count, finalize, reasoning, started_at }`
  - `ConductorArtifact { node: 'conductor', iterations: ConductorIteration[] }`
  - `ToolRunnerIteration { conductor_iteration, tools_run, errors, started_at }`
  - `ToolRunnerArtifact { node: 'tool_runner', iterations: ToolRunnerIteration[] }`
  - `StatusResponse` without `graph`

- [ ] **Step 1: Add ConductorIteration and update ConductorArtifact**

In `apps/frontend/src/api/types.ts`, add `ConductorIteration` before `ConductorArtifact`, and replace `ConductorArtifact`:

```typescript
export interface ConductorIteration {
  iteration: number
  tool_calls: ToolCall[]
  findings_count: number
  finalize: boolean
  reasoning: string
  started_at: string
}

export interface ConductorArtifact extends BaseArtifact {
  node: 'conductor'
  iterations: ConductorIteration[]
}
```

- [ ] **Step 2: Add ToolRunnerIteration and update ToolRunnerArtifact**

In `apps/frontend/src/api/types.ts`, add `ToolRunnerIteration` before `ToolRunnerArtifact`, and replace `ToolRunnerArtifact`:

```typescript
export interface ToolRunnerIteration {
  conductor_iteration: number
  tools_run: string[]
  errors: ToolError[]
  started_at: string
}

export interface ToolRunnerArtifact extends BaseArtifact {
  node: 'tool_runner'
  iterations: ToolRunnerIteration[]
}
```

- [ ] **Step 3: Remove GraphInfo types and graph from StatusResponse**

In `apps/frontend/src/api/types.ts`:

1. Remove these three interfaces entirely:
```typescript
export interface GraphNodeInfo { ... }
export interface GraphEdgeInfo { ... }
export interface GraphInfo { ... }
```

2. Remove `graph: GraphInfo` from `StatusResponse`:
```typescript
export interface StatusResponse {
  trace_id: string
  status: JobStatus
  metadata: JobMetadata
  completed_at: string | null
  results: JobResult | null
  error: string | null
  artifacts: Artifact[]
  cost: number | null
}
```

- [ ] **Step 4: Type-check**

```bash
cd apps/frontend && pnpm run build 2>&1 | head -40
```

Expected: errors about `graph` references in graphStateMapper.ts and graphDefinition.ts — these are fixed in Tasks 4 and 5. If errors are only in those two files, proceed.

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/src/api/types.ts
git commit -m "feat(frontend): update artifact types to per-iteration arrays, remove GraphInfo"
```

---

### Task 4: Frontend graphDefinition — NodeId as string, add nodeKind

**Files:**
- Modify: `apps/frontend/src/components/graph/graphDefinition.ts`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `type NodeId = string`
  - `type NodeKind = { kind: 'START' } | { kind: 'END' } | { kind: 'prep' } | { kind: 'hitl_gate' } | { kind: 'report_builder' } | { kind: 'discovery' } | { kind: 'conductor'; iter: number } | { kind: 'tool'; name: string; iter: number } | { kind: 'unknown' }`
  - `function nodeKind(id: string): NodeKind`
  - Keep: `GraphNodeDef`, `GraphEdgeDef`, `GraphNodeState`, `GraphRenderData`, `NodeStatus`

- [ ] **Step 1: Rewrite graphDefinition.ts**

Replace the entire contents of `apps/frontend/src/components/graph/graphDefinition.ts` with:

```typescript
// src/components/graph/graphDefinition.ts

export type NodeId = string

export type NodeStatus = 'idle' | 'active' | 'awaiting' | 'done' | 'failed' | 'cancelled'

export interface GraphNodeDef {
  id: NodeId
  label: string
  layer: number
  isSubgraph: boolean
  laneIndex?: number
}

export interface GraphEdgeDef {
  source: NodeId
  target: NodeId
}

export interface GraphNodeState {
  id: NodeId
  def: GraphNodeDef
  status: NodeStatus
  hasDetail: boolean
}

export interface GraphRenderData {
  nodes: GraphNodeState[]
  edges: GraphEdgeDef[]
}

export type NodeKind =
  | { kind: 'START' }
  | { kind: 'END' }
  | { kind: 'prep' }
  | { kind: 'hitl_gate' }
  | { kind: 'report_builder' }
  | { kind: 'discovery' }
  | { kind: 'conductor'; iter: number }
  | { kind: 'tool'; name: string; iter: number }
  | { kind: 'unknown' }

export function nodeKind(id: string): NodeKind {
  if (id === 'START') return { kind: 'START' }
  if (id === 'END') return { kind: 'END' }
  if (id === 'prep') return { kind: 'prep' }
  if (id === 'hitl_gate') return { kind: 'hitl_gate' }
  if (id === 'report_builder') return { kind: 'report_builder' }
  if (id === 'discovery') return { kind: 'discovery' }
  const conductorMatch = id.match(/^conductor:(\d+)$/)
  if (conductorMatch) return { kind: 'conductor', iter: parseInt(conductorMatch[1], 10) }
  const toolMatch = id.match(/^tool:(.+):(\d+)$/)
  if (toolMatch) return { kind: 'tool', name: toolMatch[1], iter: parseInt(toolMatch[2], 10) }
  return { kind: 'unknown' }
}
```

- [ ] **Step 2: Type-check**

```bash
cd apps/frontend && pnpm run build 2>&1 | head -40
```

Expected: errors only in `graphStateMapper.ts` (which imports `GRAPH_NODES`, `GRAPH_EDGES`, `buildGraphDef` — removed). Proceed.

- [ ] **Step 3: Commit**

```bash
git add apps/frontend/src/components/graph/graphDefinition.ts
git commit -m "feat(frontend): NodeId as string, add nodeKind helper, remove static graph arrays"
```

---

### Task 5: Frontend graphStateMapper — full rewrite

**Files:**
- Modify (rewrite): `apps/frontend/src/components/graph/graphStateMapper.ts`

**Interfaces:**
- Consumes: `ConductorArtifact`, `ConductorIteration`, `ToolRunnerArtifact`, `ToolRunnerIteration`, `HitlGateArtifact`, `ReportArtifact` from `api/types.ts`
- Consumes: `nodeKind`, `NodeId`, `GraphNodeDef`, `GraphEdgeDef`, `GraphNodeState`, `GraphRenderData`, `NodeStatus` from `graphDefinition.ts`
- Produces: `mapResponseToGraphState(response: StatusResponse | null): GraphRenderData`

- [ ] **Step 1: Rewrite graphStateMapper.ts**

Replace the entire contents of `apps/frontend/src/components/graph/graphStateMapper.ts` with:

```typescript
// src/components/graph/graphStateMapper.ts
import type {
  StatusResponse,
  Artifact,
  ConductorArtifact,
  ConductorIteration,
  ToolRunnerArtifact,
  HitlGateArtifact,
  ReportArtifact,
} from '../../api/types'
import type { GraphRenderData, GraphNodeState, GraphEdgeDef, NodeStatus } from './graphDefinition'

export function mapResponseToGraphState(response: StatusResponse | null): GraphRenderData {
  if (!response) {
    return {
      nodes: [{
        id: 'START',
        def: { id: 'START', label: 'START', layer: 0, isSubgraph: false },
        status: 'idle',
        hasDetail: false,
      }],
      edges: [],
    }
  }
  return buildGraphFromArtifacts(response.artifacts ?? [], response.status)
}

function buildGraphFromArtifacts(artifacts: Artifact[], jobStatus: StatusResponse['status']): GraphRenderData {
  const conductorArt   = artifacts.find(a => a.node === 'conductor')     as ConductorArtifact | undefined
  const toolRunnerArt  = artifacts.find(a => a.node === 'tool_runner')   as ToolRunnerArtifact | undefined
  const prepArt        = artifacts.find(a => a.node === 'prep')
  const hitlArt        = artifacts.find(a => a.node === 'hitl_gate')     as HitlGateArtifact | undefined
  const reportArt      = artifacts.find(a => a.node === 'report_builder') as ReportArtifact | undefined

  const allIterations  = [...(conductorArt?.iterations ?? [])].sort((a, b) => a.iteration - b.iteration)
  const toolIterations = toolRunnerArt?.iterations ?? []

  // Split conductor iterations around hitl_gate using timestamps
  let preHitl  = allIterations
  let postHitl: ConductorIteration[] = []
  if (hitlArt) {
    const hitlStart = hitlArt.started_at
    preHitl  = allIterations.filter(it => it.started_at < hitlStart)
    postHitl = allIterations.filter(it => it.started_at >= hitlStart)
  }

  const nodes: GraphNodeState[] = []
  const edges: GraphEdgeDef[]   = []
  let layer = 0

  // START
  nodes.push(makeNode('START', 'START', layer++, jobStatus === 'pending' ? 'idle' : 'done'))

  // prep
  nodes.push(makeNode('prep', 'prep', layer++, simpleArtifactStatus(prepArt, jobStatus)))
  edges.push({ source: 'START', target: 'prep' })

  let prevOutputIds: string[] = ['prep']

  const appendIterations = (iterations: ConductorIteration[]) => {
    for (let i = 0; i < iterations.length; i++) {
      const iter = iterations[i]
      const conductorId = `conductor:${iter.iteration}`
      const isLastOverall = iter.iteration === allIterations.at(-1)?.iteration
      const conductorStatus: NodeStatus =
        isLastOverall && (jobStatus === 'running' || jobStatus === 'processing') ? 'active' : 'done'

      nodes.push(makeNode(conductorId, `conductor ${iter.iteration}`, layer++, conductorStatus, true))
      prevOutputIds.forEach(id => edges.push({ source: id, target: conductorId }))

      const iterTools = toolIterations.find(t => t.conductor_iteration === iter.iteration)
      if (iterTools && iterTools.tools_run.length > 0) {
        const toolIds: string[] = []
        iterTools.tools_run.forEach((toolName, laneIdx) => {
          const toolId = `tool:${toolName}:${iter.iteration}`
          const hasError = iterTools.errors.some(e => e.tool === toolName)
          nodes.push({
            id: toolId,
            def: { id: toolId, label: toolName, layer, isSubgraph: false, laneIndex: laneIdx },
            status: hasError ? 'failed' : 'done',
            hasDetail: true,
          })
          edges.push({ source: conductorId, target: toolId })
          toolIds.push(toolId)
        })
        layer++
        prevOutputIds = toolIds
      } else {
        prevOutputIds = [conductorId]
      }
    }
  }

  appendIterations(preHitl)

  // hitl_gate
  if (hitlArt) {
    const hitlStatus: NodeStatus =
      hitlArt.status === 'done' ? 'done'
      : hitlArt.messages.length > 0 ? 'awaiting'
      : 'active'
    nodes.push(makeNode('hitl_gate', 'hitl_gate', layer++, hitlStatus, hitlArt.messages.length > 0))
    prevOutputIds.forEach(id => edges.push({ source: id, target: 'hitl_gate' }))
    prevOutputIds = ['hitl_gate']

    if (postHitl.length > 0) {
      appendIterations(postHitl)
    }
  }

  // report_builder
  if (reportArt) {
    const reportStatus = simpleArtifactStatus(reportArt, jobStatus)
    nodes.push(makeNode('report_builder', 'report_builder', layer++, reportStatus, !!(reportArt as ReportArtifact).output))
    prevOutputIds.forEach(id => edges.push({ source: id, target: 'report_builder' }))
    prevOutputIds = ['report_builder']
  }

  // END
  if (jobStatus === 'done' || jobStatus === 'failed' || jobStatus === 'cancelled') {
    const endStatus: NodeStatus =
      jobStatus === 'done' ? 'done' : jobStatus === 'failed' ? 'failed' : 'cancelled'
    nodes.push(makeNode('END', 'END', layer++, endStatus))
    prevOutputIds.forEach(id => edges.push({ source: id, target: 'END' }))
  }

  return { nodes, edges }
}

function makeNode(
  id: string,
  label: string,
  layer: number,
  status: NodeStatus,
  hasDetail = false,
  laneIndex?: number,
): GraphNodeState {
  return {
    id,
    def: { id, label, layer, isSubgraph: false, laneIndex },
    status,
    hasDetail,
  }
}

function simpleArtifactStatus(artifact: Artifact | undefined, jobStatus: StatusResponse['status']): NodeStatus {
  if (!artifact) return jobStatus === 'cancelled' ? 'cancelled' : 'idle'
  if (artifact.status === 'done')      return 'done'
  if (artifact.status === 'failed')    return 'failed'
  if (artifact.status === 'cancelled') return 'cancelled'
  return 'active'
}
```

- [ ] **Step 2: Type-check**

```bash
cd apps/frontend && pnpm run build 2>&1 | head -40
```

Expected: errors only in panel files that still reference old `ConductorArtifact` fields (`iteration`, `reasoning`, `tool_calls` as flat fields) — fixed in Task 6. No errors in graphStateMapper itself.

- [ ] **Step 3: Commit**

```bash
git add apps/frontend/src/components/graph/graphStateMapper.ts
git commit -m "feat(frontend): rewrite graphStateMapper to build graph from artifacts"
```

---

### Task 6: Frontend panels and node registry

**Files:**
- Create: `apps/frontend/src/components/graph/panels/ToolPanel.tsx`
- Modify: `apps/frontend/src/components/graph/panels/ConductorPanel.tsx`
- Modify: `apps/frontend/src/components/graph/nodeRegistry.ts`

**Interfaces:**
- Consumes: `nodeKind` from `graphDefinition.ts`
- Consumes: `ConductorArtifact`, `ConductorIteration`, `ToolRunnerArtifact` from `api/types.ts`
- Consumes: `PanelProps` from `panels/types.ts` — `{ nodeId: NodeId, results: JobResult | null, artifacts: Artifact[] }`

- [ ] **Step 1: Create ToolPanel.tsx**

Create `apps/frontend/src/components/graph/panels/ToolPanel.tsx`:

```typescript
import type { PanelProps } from './types'
import type { ToolRunnerArtifact } from '../../../api/types'
import { nodeKind } from '../graphDefinition'

export function ToolPanel({ nodeId, artifacts }: PanelProps) {
  const kind = nodeKind(nodeId)
  if (kind.kind !== 'tool') return null

  const toolRunnerArt = artifacts.find(a => a.node === 'tool_runner') as ToolRunnerArtifact | undefined
  const iteration = toolRunnerArt?.iterations.find(i => i.conductor_iteration === kind.iter)
  const errorEntry = iteration?.errors.find(e => e.tool === kind.name)

  return (
    <div className="space-y-2">
      <div className="flex gap-4 font-mono text-xs">
        <span className="text-(--color-muted)">Tool</span>
        <span className="text-(--color-accent)">{kind.name}</span>
      </div>
      <div className="flex gap-4 font-mono text-xs">
        <span className="text-(--color-muted)">Conductor iteration</span>
        <span className="text-(--color-text)">{kind.iter}</span>
      </div>
      {errorEntry ? (
        <div className="rounded border border-(--color-error)/40 bg-(--color-error)/5 p-3">
          <p className="mb-1 font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">Error</p>
          <p className="font-mono text-xs text-(--color-error)">{errorEntry.error}</p>
        </div>
      ) : (
        <p className="font-mono text-xs text-(--color-muted)">Completed successfully.</p>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Rewrite ConductorPanel.tsx**

Replace the entire contents of `apps/frontend/src/components/graph/panels/ConductorPanel.tsx`:

```typescript
import type { PanelProps } from './types'
import type { ConductorArtifact } from '../../../api/types'
import { nodeKind } from '../graphDefinition'

export function ConductorPanel({ nodeId, artifacts }: PanelProps) {
  const conductorArt = artifacts.find(a => a.node === 'conductor') as ConductorArtifact | undefined
  if (!conductorArt?.iterations.length) {
    return <p className="font-mono text-xs text-(--color-muted)">No data yet.</p>
  }

  const kind = nodeKind(nodeId)
  const iter = kind.kind === 'conductor'
    ? conductorArt.iterations.find(i => i.iteration === kind.iter)
    : conductorArt.iterations.at(-1)

  if (!iter) return <p className="font-mono text-xs text-(--color-muted)">No data yet.</p>

  return (
    <div className="space-y-3">
      <div className="flex gap-4 font-mono text-xs">
        <span className="text-(--color-muted)">Iteration</span>
        <span className="text-(--color-text)">{iter.iteration}</span>
      </div>
      <div className="flex gap-4 font-mono text-xs">
        <span className="text-(--color-muted)">Findings</span>
        <span className="text-(--color-text)">{iter.findings_count}</span>
      </div>
      {iter.reasoning && (
        <div className="rounded border border-(--color-border) bg-(--color-surface-raised) p-3">
          <p className="mb-1 font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">Reasoning</p>
          <p className="font-mono text-xs text-(--color-text) leading-relaxed">{iter.reasoning}</p>
        </div>
      )}
      {iter.tool_calls.length > 0 && (
        <div>
          <p className="mb-1 font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">Tool calls</p>
          <ul className="space-y-1">
            {iter.tool_calls.map((tc, i) => (
              <li key={i} className="font-mono text-xs text-(--color-text)">
                <span className="text-(--color-accent)">{tc.tool}</span>
                {Object.keys(tc.args).length > 0 && (
                  <span className="text-(--color-muted)"> ({JSON.stringify(tc.args)})</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Rewrite nodeRegistry.ts**

Replace the entire contents of `apps/frontend/src/components/graph/nodeRegistry.ts`:

```typescript
import type { ComponentType } from 'react'
import type { PanelProps } from './panels/types'
import { nodeKind } from './graphDefinition'
import { ConductorPanel } from './panels/ConductorPanel'
import { ToolPanel } from './panels/ToolPanel'
import { HitlGatePanel } from './panels/HitlGatePanel'
import { DiscoveryPanel } from './panels/DiscoveryPanel'
import { PlannerPanel } from './panels/PlannerPanel'
import { SkillExecutorPanel } from './panels/SkillExecutorPanel'
import { CorrelatorPanel } from './panels/CorrelatorPanel'
import { FindingReviewerPanel } from './panels/FindingReviewerPanel'
import { ReportBuilderPanel } from './panels/ReportBuilderPanel'

export type { PanelProps }

type PanelComponent = ComponentType<PanelProps>

export function getPanelComponent(id: string): PanelComponent | undefined {
  const k = nodeKind(id).kind
  if (k === 'conductor')       return ConductorPanel
  if (k === 'tool')            return ToolPanel
  if (id === 'hitl_gate')      return HitlGatePanel
  if (id === 'report_builder') return ReportBuilderPanel
  if (id === 'discovery')      return DiscoveryPanel
  if (id === 'investigation_planner') return PlannerPanel
  if (id === 'skill_executor') return SkillExecutorPanel
  if (id === 'evidence_correlator')   return CorrelatorPanel
  if (id === 'finding_reviewer')      return FindingReviewerPanel
  return undefined
}
```

- [ ] **Step 4: Full type-check**

```bash
cd apps/frontend && pnpm run build 2>&1 | head -60
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/src/components/graph/panels/ToolPanel.tsx \
        apps/frontend/src/components/graph/panels/ConductorPanel.tsx \
        apps/frontend/src/components/graph/nodeRegistry.ts
git commit -m "feat(frontend): artifact-driven panels — ToolPanel, updated ConductorPanel, prefix registry"
```
