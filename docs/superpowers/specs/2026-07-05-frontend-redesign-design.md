# Frontend Redesign — Design Spec

**Date:** 2026-07-05
**Scope:** Full adaptation of the React frontend to the new backend architecture, plus UX redesign.
**Approach:** Targeted rebuild — keep design tokens, UI primitives, D3 graph, and `usePolling`; rebuild all pages and the type system from scratch around the correct API contract.

---

## 1. Information Architecture & Routes

### Route Map

| Route | Page | Action |
|---|---|---|
| `/` | `LandingPage` | Keep + polish |
| `/new` | `NewAnalysisPage` | Replace `/scan` — new input model |
| `/jobs` | `JobsListPage` | Keep structure, minor cleanup |
| `/jobs/:traceId` | `ExecutionPage` | Replace `JobDetailPage` + `PlanPage` (merged) |
| `/jobs/:traceId/report` | `ReportPage` | New |

**Removed routes:** `/scan`, `/jobs/:traceId/plan`

### Navigation (Header)

- Logo → `/`
- "Executions" link → `/jobs`
- "New analysis" button → `/new`

### User Flows

```
Landing → /new → submit → /jobs/:traceId (execution + HITL)
                                │ on done
                                └─ /jobs/:traceId/report

/jobs → click row (done)   → /jobs/:traceId/report
/jobs → click row (other)  → /jobs/:traceId

/jobs/:traceId/report → "← Execution view" → /jobs/:traceId
```

### Key Principle

`ExecutionPage` is the permanent home of a job once created. It handles every status — pending, running, awaiting_approval (chat overlay appears), done (footer shows "View report" link), failed, cancelled. No more route-based state machines or redirect timers.

---

## 2. Type System

**File:** `src/api/types.ts` — full replacement.

### Request

```typescript
interface AnalysisRequest {
  repo_url: string
  concern: string
}
```

### Job

```typescript
type JobStatus =
  | 'pending' | 'running' | 'processing'
  | 'awaiting_approval' | 'done' | 'failed' | 'cancelled'

interface JobMetadata {
  repo_url: string
  concern: string
}

interface JobListItem {
  trace_id: string
  status: JobStatus
  concern: string
  created_at: string
  completed_at: string | null
}

interface JobsListResponse {
  items: JobListItem[]
  total: number; page: number; limit: number; pages: number
}
```

### Status Response

```typescript
interface StatusResponse {
  trace_id: string
  status: JobStatus
  metadata: JobMetadata
  completed_at: string | null
  results: JobResult | null
  artifacts: Artifact[]
  graph: GraphInfo
}

interface JobResult {
  discovery: DiscoveryResult
  risk_findings: RiskFinding[]
  analysis_report: AnalysisReport | null
  review_approved: boolean | null
  review_iterations: number | null
}

interface DiscoveryResult {
  project_metadata: ProjectMetadata | null
  manifest_files: string[] | null
  discovery_summary: string | null
  discovery_error: string | null
  sbom_result_id: string | null
  sbom_error: string | null
}

interface ProjectMetadata {
  name: string
  package_manager: string
  direct_dependencies_count: number
  transitive_dependencies_count: number
}
```

### Artifacts

```typescript
type ArtifactStatus = 'running' | 'done' | 'failed' | 'cancelled'

interface ArtifactMessage {
  role: 'assistant' | 'human'
  content: string
  created_at: string
  action?: 'approve' | 'change' | 'cancel'
}

interface BaseArtifact {
  node: string
  status: ArtifactStatus
  started_at: string
  completed_at: string | null
}

interface DiscoveryArtifact extends BaseArtifact {
  node: 'discovery'
  steps: string[]
}

interface PlannerArtifact extends BaseArtifact {
  node: 'investigation_planner'
  data?: { plan: PlanData }
  messages: ArtifactMessage[]
}

interface PlanData {
  rationale: string
  hypotheses: Hypothesis[]
  dep_filter: string[] | null
}

interface Hypothesis {
  id: string; dep_name: string; statement: string
  risk_theme: string; rationale: string; skills: string[]
  status: 'open' | 'supported' | 'refuted' | 'inconclusive'
  confidence: number | null
}

interface CollectorArtifact extends BaseArtifact {
  node: 'evidence_collector'
  steps: string[]
}

interface CorrelatorArtifact extends BaseArtifact {
  node: 'evidence_correlator'
  data?: { findings_count: number; contradictions_count: number; deps_covered: string[] }
}

interface ReviewerArtifact extends BaseArtifact {
  node: 'finding_reviewer'
  data?: { risk_findings: RiskFinding[] }
  output?: { review_approved: boolean; reviewer_feedback: string | null }
  messages: ArtifactMessage[]
}

interface ReportArtifact extends BaseArtifact {
  node: 'report_builder'
  output?: AnalysisReport
}

type Artifact =
  | DiscoveryArtifact | PlannerArtifact | CollectorArtifact
  | CorrelatorArtifact | ReviewerArtifact | ReportArtifact
```

### Report

```typescript
type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'

interface AnalysisReport {
  concern: string
  generated_at: string
  overall_risk_level: Severity | 'none'
  summary: { total_deps: number; critical: number; high: number; medium: number; low: number }
  findings: ReportFinding[]
  recommendations: string[]
  contradictions: { description: string; resolution: string }[]
}

interface ReportFinding {
  dep_name: string
  risk_score: number        // 0–10
  confidence: number        // 0–1
  severity: Severity
  summary: string
  recommendation: string | null
  alternatives: string[]
  supporting_evidence_count: number
  contradictions_count: number
  missing_evidence: string[]
}

interface RiskFinding extends ReportFinding {
  hypotheses: Hypothesis[]
  supporting_evidence: string[]
  contradictions: ContradictionReport[]
}

interface ContradictionReport {
  evidence_ids: string[]
  description: string
  resolution: string
  adjusted_confidence: number
}
```

### Graph

```typescript
interface GraphInfo {
  nodes: GraphNodeInfo[]
  edges: GraphEdgeInfo[]
}
interface GraphNodeInfo { id: string; type: 'terminal' | 'backbone' | 'subgraph'; order: number }
interface GraphEdgeInfo { source: string; target: string }
```

**Deleted types:** `LockFileName`, old `JobMetadata` (package_json/lock_file), `DependencyTree`, `DepTreeDatum`, `DependencyEntry`, `DiscoveryResult` (old shape), `SubgraphResult`, `AnalysisResult`, `Proposal`, old `ArtifactInfo`.

---

## 3. API Client

**File:** `src/api/analyze.ts` — replace content.

```typescript
submitAnalysis(req: AnalysisRequest): Promise<{ trace_id: string; status: JobStatus }>
  → POST /analyze

getAnalysisStatus(traceId: string): Promise<StatusResponse>
  → GET /analyze/:traceId

sendChatMessage(traceId: string, message: string): Promise<void>
  → POST /analyze/:traceId/chat  { message }

getJobs(page, limit, status?, traceId?): Promise<JobsListResponse>
  → GET /jobs
```

**Removed:** `approvePlan` (endpoint does not exist in new backend).

---

## 4. State & Data Layer

### Hooks

**`usePolling`** — no changes. Keep as-is.

**`useJobStatus(traceId: string)`**

Wraps `usePolling`. Stop condition: `done | failed | cancelled | awaiting_approval`. Returns `{ data, isPolling, error, resume }`. `resume()` restarts polling after a chat message is sent.

**`useJobSubmit()`**

Calls `POST /analyze`. On success, calls `navigate(/jobs/:traceId)`. No polling.

**`useChat(traceId: string, opts: { autopilot: boolean, onSent: () => void })`**

- Derives `activeGate` from artifacts (pure function, see below).
- `send(message)`: calls `POST /analyze/:traceId/chat`, then calls `opts.onSent()` (which triggers `resume()` in `useJobStatus`).
- When `autopilot === true` and `activeGate !== null`: auto-calls `send("Yes, proceed")` without user interaction. Uses a `useEffect` with `activeGate` as dependency; guards with a ref to prevent double-firing.
- Returns `{ activeGate, send, isSending }`.

**`useReport(traceId: string)`**

One-shot `GET /analyze/:traceId`. No polling. Extracts `results.analysis_report`. Returns `{ report, isLoading, error }`.

### Active Gate Detection (pure utility)

```typescript
// src/lib/getActiveGate.ts
type Gate = 'investigation_planner' | 'finding_reviewer'

function getActiveGate(artifacts: Artifact[]): Gate | null {
  const gates: Gate[] = ['investigation_planner', 'finding_reviewer']
  return gates.find(node => {
    const a = artifacts.find(a => a.node === node) as PlannerArtifact | ReviewerArtifact | undefined
    return a?.status === 'running' && a.messages?.length > 0
  }) ?? null
}
```

### Autopilot Persistence

`autopilot` is a boolean stored in `localStorage` key `deprisk.autopilot`. Toggled by checkbox in `NewAnalysisPage`, written to storage on change. `ExecutionPage` reads this key directly on mount — no `location.state` passing, which would be lost on page refresh.

### Samples

**File:** `src/data/samples.ts` — replace content.

New samples are `{ id, label, description, repo_url, concern }`. Point to real or plausible GitHub repos that exercise different skill paths. Remove all `package_json`/`lock_file` content.

---

## 5. Pages

### Landing Page (`/`) — Polish

Keep hero, feature grid, how-it-works steps, CTA banner. Changes:

- Update "how it works" steps: `Enter repo URL → AI builds plan → Review & approve → Get report`. Remove "Upload package.json" step.
- CTA navigates to `/new`.
- Optionally replace emoji icons with simple inline SVGs for polish (low priority).

### New Analysis Page (`/new`)

Two-column layout on desktop (≥ md), single column on mobile.

**Left column — form:**
1. Repo URL input (text, required, placeholder `https://github.com/org/repo`)
2. Concern textarea (3 rows, required)
3. Pre-built concern pills — clicking a pill replaces the textarea content: "Supply chain risks", "Known CVEs", "License compliance", "Maintainer trust", "Blast radius"
4. Autopilot checkbox — label: "Autopilot mode: AI auto-approves both review gates and runs to completion". Persisted in `localStorage`.
5. "Run analysis →" submit button

**Right column — sample repos:**
Cards with `repo_url` + `concern` pre-filled. Clicking a card fills both fields. Layout: 2-column grid of cards, same visual style as current sample cards.

**Validation:** Both fields required. URL must start with `https://`. Show inline errors below each field.

**On submit:** calls `useJobSubmit()` → navigates to `/jobs/:traceId` with `autopilot` in `location.state`.

### Jobs List Page (`/jobs`) — Minor updates

- "New analysis →" button in page header (right-aligned), links to `/new`.
- Status filter: add `awaiting_approval` and `processing` options.
- Row click: `done` → `/jobs/:traceId/report`; all other statuses → `/jobs/:traceId`.
- Concern column: clamp to 2 lines (`line-clamp-2`), full text on hover via `title` attribute.

### Execution Page (`/jobs/:traceId`) — Full rebuild

**Layout:**

```
┌─ header bar ──────────────────────────────────────────────────────┐
│  ← Executions   [concern, truncated]   [status badge]   [● Chat] │
└───────────────────────────────────────────────────────────────────┘
┌─ meta strip (collapsed, click to expand) ─────────────────────────┐
│  Trace ID  ·  repo_url  ·  started  ·  duration                  │
└───────────────────────────────────────────────────────────────────┘
┌─ execution graph (primary content, full width) ───────────────────┐
│  D3 graph                                                         │
│  node detail side panel slides in from right when node clicked    │
└───────────────────────────────────────────────────────────────────┘
┌─ status footer (conditional) ─────────────────────────────────────┐
│  done     → [ View full report → ]                                │
│  failed   → error message from results or artifact                │
│  cancelled → "Analysis was cancelled"                             │
└───────────────────────────────────────────────────────────────────┘
```

**Chat button:** `[● Chat]` in header. Dot is grey (no active gate), amber-pulsing (gate waiting), hidden with `AUTOPILOT` badge when autopilot mode is on. Opens `ChatOverlay`.

**Node detail panel:** Slides in from right, overlays ~40% of graph width. Has close button (✕). Does not push layout. Dismissible by clicking ✕ or pressing Escape.

**Polling:** Managed by `useJobStatus`. Resumes after `send()` in `useChat`.

**Autopilot:** Read from `location.state.autopilot` on mount. If true, show `AUTOPILOT` badge; `useChat` handles auto-approval silently.

### Report Page (`/jobs/:traceId/report`) — New

```
┌─ breadcrumb ──────────────────────────────────────────────────────┐
│  ← Execution view                                                 │
└───────────────────────────────────────────────────────────────────┘
┌─ report header ───────────────────────────────────────────────────┐
│  [project name or repo name]  ·  Overall risk: [CRITICAL badge]   │
│  Concern: "..."               ·  Generated: date  ·  N findings   │
└───────────────────────────────────────────────────────────────────┘
┌─ summary strip ───────────────────────────────────────────────────┐
│  [N CRITICAL]  [N HIGH]  [N MEDIUM]  [N LOW]                     │
└───────────────────────────────────────────────────────────────────┘
┌─ findings accordion ──────────────────────────────────────────────┐
│  ▼ dep_name  ·  [CRITICAL]  ·  9.2/10 ████░  ·  87% confidence   │
│    summary text                                                    │
│    Recommendation: ...    Alternatives: ...                        │
│    [2 evidence]  [1 contradiction]  [missing: ...]               │
│  ▶ dep_name  ...                                                   │
└───────────────────────────────────────────────────────────────────┘
┌─ recommendations ─────────────────────────────────────────────────┐
│  1. ...  2. ...                                                    │
└───────────────────────────────────────────────────────────────────┘
┌─ contradictions (if any) ─────────────────────────────────────────┐
│  description  ·  resolution badge                                  │
└───────────────────────────────────────────────────────────────────┘
```

Findings sorted by `risk_score` descending (backend already sends them sorted). Severity badges: critical=red, high=orange, medium=yellow, low=blue, info=grey. Risk score shown as `8.2/10` + a small filled progress bar. Accordion: first item expanded by default.

Data loaded by `useReport(traceId)`. If report is not yet available (job not done), redirect to `/jobs/:traceId`.

---

## 6. Graph & Node Panels

### `graphDefinition.ts`

```typescript
type NodeId =
  | 'START' | 'discovery' | 'investigation_planner'
  | 'skill_dispatcher' | 'skill_executor'
  | 'evidence_collector' | 'evidence_correlator'
  | 'finding_reviewer' | 'report_builder' | 'END'

type NodeStatus = 'idle' | 'active' | 'awaiting' | 'done' | 'failed' | 'cancelled'
```

- `awaiting`: HITL gate open — amber pulsing border (distinct from `active`).
- `cancelled`: muted/dimmed style.
- Static `GRAPH_NODES`/`GRAPH_EDGES` updated to reflect 8 backbone nodes. `buildGraphDef` remains the primary builder.

### `graphStateMapper.ts`

`deriveStatus` rewritten:

```
'START' → 'done' if jobStatus !== 'pending', else 'idle'
'END'   → 'done' if done, 'failed' if failed, else 'idle'
others  → from artifact.status:
            running → 'active'
            running + node in ['investigation_planner','finding_reviewer']
                    + messages.length > 0 → 'awaiting'
            done    → 'done'
            failed  → 'failed'
            no artifact + jobStatus === 'cancelled' → 'cancelled'
            no artifact → 'idle'
```

`hasDetail` rewritten to match new node IDs and artifact schemas.

### `nodeRegistry.ts`

```typescript
discovery             → DiscoveryPanel
investigation_planner → PlannerPanel
skill_executor        → SkillExecutorPanel   // NEW
evidence_correlator   → CorrelatorPanel       // NEW
finding_reviewer      → FindingReviewerPanel  // NEW
report_builder        → ReportBuilderPanel    // rewrite
// skill_dispatcher, evidence_collector: no panel
```

### Panel Content

| Panel | Key content |
|---|---|
| `DiscoveryPanel` | `steps[]` as checklist, project metadata (name, package_manager, dep counts), `discovery_summary` prose |
| `PlannerPanel` | Chat transcript from `messages[]`, collapsible hypothesis list (dep_name, statement, assigned skills) from `artifact.data.plan` |
| `SkillExecutorPanel` | List of `evidence_collector.steps` (e.g. `vulnerability:lodash`) with count badge. Panel is registered on `skill_executor` but reads the `evidence_collector` artifact, since `skill_executor` instances are ephemeral and the collector aggregates their output. |
| `CorrelatorPanel` | `findings_count`, `contradictions_count`, `deps_covered` as tag chips |
| `FindingReviewerPanel` | Mini findings list (dep_name + severity) from `artifact.data.risk_findings`, chat transcript if Gate 2 was triggered |
| `ReportBuilderPanel` | "Report ready" confirmation + `Link` to `/jobs/:traceId/report` |

**Deleted:** `DependencyTree.tsx`, `DependencyPanel.tsx`, `SubgraphPanel.tsx`, `FinalReportPanel.tsx` (old), `PlannerPanel.tsx` (old/rewrite).

---

## 7. Chat Overlay

**Component:** `src/components/chat/ChatOverlay.tsx`

### Layout

Centered modal, 600px wide, 70vh tall. Non-dismissible when an active gate is waiting (`activeGate !== null`). Dismissible otherwise (manual history view).

```
┌─ backdrop (dimmed, blocks graph interaction) ─────────────────────┐
│  ┌─ chat panel ────────────────────────────────────────────────┐  │
│  │  ┌─ header ─────────────────────────────────────────────┐   │  │
│  │  │  [gate label]                               [✕ / —]  │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  │  ┌─ messages (scrollable) ──────────────────────────────┐   │  │
│  │  │  [assistant bubble — markdown rendered]              │   │  │
│  │  │  [human bubble — right-aligned]                      │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  │  ┌─ quick actions (Gate 1 only) ────────────────────────┐   │  │
│  │  │  [Yes, proceed]     [Cancel analysis]                │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  │  ┌─ input ──────────────────────────────────────────────┐   │  │
│  │  │  [textarea…]                              [Send]      │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  └─────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

### Props

```typescript
interface ChatOverlayProps {
  open: boolean
  onClose: () => void
  activeGate: 'investigation_planner' | 'finding_reviewer' | null
  messages: ArtifactMessage[]
  isSending: boolean
  onSend: (message: string) => Promise<void>
}
```

### Gate Labels

| Gate | Header label |
|---|---|
| `investigation_planner` | "Investigation Plan Review" |
| `finding_reviewer` | "Risk Findings Review" |
| `null` (history) | "Conversation History" |

### Behavior

- Overlay opens automatically when `activeGate !== null`.
- `[●Chat]` button in `ExecutionPage` header: grey dot (no gate), amber pulsing (gate active), replaced by `AUTOPILOT` badge when autopilot enabled.
- Gate 1: shows "Yes, proceed" + "Cancel analysis" quick action buttons.
- Gate 2: no quick cancel — any message is treated as acknowledgement.
- Backdrop click: dismissible only when `activeGate === null`.
- Markdown: assistant message `content` rendered with `marked` (add as dependency). Human messages rendered as plain text.

### Autopilot Logic (in `useChat`)

```typescript
useEffect(() => {
  if (!autopilot || !activeGate || hasFiredRef.current) return
  hasFiredRef.current = true
  void send('Yes, proceed')
}, [activeGate, autopilot])

// Reset hasFiredRef when activeGate changes to a new gate
useEffect(() => { hasFiredRef.current = false }, [activeGate])
```

---

## 8. Component Inventory

### Keep (no changes)

`usePolling`, `Badge`, `Button`, `Input`, `Modal`, `Select`, `Spinner`, `Textarea`, `PageWrapper`, `Header` (update nav links), `lib/utils.ts`, `api/client.ts`, design tokens in `index.css`.

### Keep (update internals)

`ExecutionGraph.tsx` — D3 logic intact, add `awaiting` + `cancelled` status colors.
`NodeDetailPanel.tsx` — panel registry lookup stays, update slide-in behavior.
`graphStateMapper.ts` — full rewrite of `deriveStatus` and `hasDetail`.
`graphDefinition.ts` — update `NodeId` type, static fallback nodes, `buildGraphDef` stays.
`JobsListPage.tsx` — add new filters, row click routing, new button.
`LandingPage.tsx` — update steps text and CTA route.
`Header.tsx` — update nav links.

### Rewrite

`src/api/types.ts`, `src/api/analyze.ts`, `src/hooks/useAnalysis.ts` → replace with `useJobStatus`, `useJobSubmit`, `useChat`, `useReport`, `src/hooks/usePolling.ts` stays.
`src/data/samples.ts` — new sample shape.
`src/components/graph/nodeRegistry.ts` — new panel map.
`src/components/graph/panels/DiscoveryPanel.tsx` — update to artifact.steps.
`src/components/graph/panels/PlannerPanel.tsx` — full rewrite.

### New

`src/pages/NewAnalysisPage.tsx`
`src/pages/ExecutionPage.tsx`
`src/pages/ReportPage.tsx`
`src/components/chat/ChatOverlay.tsx`
`src/components/graph/panels/SkillExecutorPanel.tsx`
`src/components/graph/panels/CorrelatorPanel.tsx`
`src/components/graph/panels/FindingReviewerPanel.tsx`
`src/components/graph/panels/ReportBuilderPanel.tsx`
`src/lib/getActiveGate.ts`

### Delete

`src/pages/ScanPage.tsx`
`src/pages/PlanPage.tsx`
`src/pages/JobDetailPage.tsx`
`src/components/analysis/ScanModal.tsx`
`src/components/analysis/AnalysisForm.tsx`
`src/components/analysis/AnalysisResult.tsx`
`src/components/analysis/AnalysisStatus.tsx`
`src/components/analysis/PlanApproval.tsx`
`src/components/analysis/DependencyTree.tsx`
`src/components/graph/panels/SubgraphPanel.tsx`
`src/components/graph/panels/FinalReportPanel.tsx`
`src/hooks/useAnalysis.ts`

---

## 9. Dependencies

**Add:** `marked` (markdown rendering in ChatOverlay assistant messages).

**No changes:** `d3`, `react-router-dom`, `tailwindcss`, `clsx`, `tailwind-merge`.

**No new state library:** hooks + props are sufficient for the application scale.

---

## 10. File Structure (final)

```
src/
  api/
    client.ts          (keep)
    types.ts           (rewrite)
    analyze.ts         (rewrite)
  components/
    chat/
      ChatOverlay.tsx  (new)
    graph/
      ExecutionGraph.tsx     (update)
      graphDefinition.ts     (update)
      graphStateMapper.ts    (rewrite)
      nodeRegistry.ts        (rewrite)
      NodeDetailPanel.tsx    (update)
      panels/
        DiscoveryPanel.tsx   (update)
        PlannerPanel.tsx     (rewrite)
        SkillExecutorPanel.tsx   (new)
        CorrelatorPanel.tsx      (new)
        FindingReviewerPanel.tsx (new)
        ReportBuilderPanel.tsx   (new)
    layout/
      Header.tsx         (update nav)
      PageWrapper.tsx    (keep)
    ui/                  (keep all)
  data/
    samples.ts           (rewrite)
  hooks/
    usePolling.ts        (keep)
    useJobStatus.ts      (new)
    useJobSubmit.ts      (new)
    useChat.ts           (new)
    useReport.ts         (new)
  lib/
    utils.ts             (keep)
    getActiveGate.ts     (new)
  pages/
    LandingPage.tsx      (update)
    NewAnalysisPage.tsx  (new)
    JobsListPage.tsx     (update)
    ExecutionPage.tsx    (new)
    ReportPage.tsx       (new)
  App.tsx                (update routes)
  index.css              (keep)
  main.tsx               (keep)
```
