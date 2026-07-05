# Frontend Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the React frontend to match the new backend API contract, merge the fragmented routing into a single execution page, and add an integrated chat overlay, autopilot mode, and a structured report page.

**Architecture:** Targeted rebuild — keep design tokens, UI primitives (`Badge`, `Button`, etc.), D3 graph component, and `usePolling`; replace all page components, the type system, and the hooks layer. Each task produces a type-checking codebase and is independently committable.

**Tech Stack:** React 18, TypeScript, Vite, Tailwind CSS v4, D3 v7, React Router v7, `marked` (new).

## Global Constraints

- Package manager: `pnpm` — always `pnpm add`, never `npm install`
- Type-check command: `pnpm type-check` (runs `tsc --noEmit`)
- Lint command: `pnpm lint`
- Dev server: `pnpm dev` (port 5173)
- Design tokens: use CSS variables (`--color-border`, `--color-accent`, `--color-surface`, `--color-text`, `--color-muted`, `--color-error`, `--color-surface-raised`) — never hardcode colors except status-specific greens/reds already in the codebase
- Font classes: `font-mono` (IBM Plex Mono) and `font-display` (Syne) — never use `font-sans`
- No new state management library — hooks + props only
- All API calls go through `src/api/analyze.ts`; `src/api/client.ts` provides `apiClient` only
- Backend base URL: `http://localhost:8000` (via `VITE_API_URL` env var)
- Spec: `docs/superpowers/specs/2026-07-05-frontend-redesign-design.md`

---

## File Map

```
REPLACE entirely:
  src/api/types.ts
  src/api/analyze.ts
  src/data/samples.ts
  src/components/graph/graphDefinition.ts
  src/components/graph/graphStateMapper.ts
  src/components/graph/nodeRegistry.ts
  src/components/graph/panels/DiscoveryPanel.tsx
  src/components/graph/panels/PlannerPanel.tsx

MODIFY:
  src/App.tsx                    (routes)
  src/components/layout/Header.tsx  (nav links)
  src/components/graph/ExecutionGraph.tsx  (awaiting/cancelled status colors)
  src/components/graph/NodeDetailPanel.tsx  (slide-in behavior)
  src/pages/JobsListPage.tsx     (minor updates)
  src/pages/LandingPage.tsx      (step text + CTA route)

CREATE:
  src/lib/getActiveGate.ts
  src/hooks/useJobStatus.ts
  src/hooks/useJobSubmit.ts
  src/hooks/useChat.ts
  src/hooks/useReport.ts
  src/components/chat/ChatOverlay.tsx
  src/components/graph/panels/SkillExecutorPanel.tsx
  src/components/graph/panels/CorrelatorPanel.tsx
  src/components/graph/panels/FindingReviewerPanel.tsx
  src/components/graph/panels/ReportBuilderPanel.tsx
  src/pages/NewAnalysisPage.tsx
  src/pages/ExecutionPage.tsx
  src/pages/ReportPage.tsx

DELETE (after routing is updated):
  src/pages/ScanPage.tsx
  src/pages/PlanPage.tsx
  src/pages/JobDetailPage.tsx
  src/hooks/useAnalysis.ts
  src/components/analysis/ScanModal.tsx
  src/components/analysis/AnalysisForm.tsx
  src/components/analysis/AnalysisResult.tsx
  src/components/analysis/AnalysisStatus.tsx
  src/components/analysis/PlanApproval.tsx
  src/components/analysis/DependencyTree.tsx
  src/components/graph/panels/SubgraphPanel.tsx
  src/components/graph/panels/FinalReportPanel.tsx
```

---

## Task 1: Type System + API Client + Routing Scaffold

Replace the type system, update the API layer, wire up new routes to stub pages, delete all dead files. After this task the app compiles, every route renders a placeholder, and the old file tree is gone.

**Files:**
- Replace: `src/api/types.ts`
- Replace: `src/api/analyze.ts`
- Modify: `src/App.tsx`
- Modify: `src/components/layout/Header.tsx`
- Create stubs: `src/pages/NewAnalysisPage.tsx`, `src/pages/ExecutionPage.tsx`, `src/pages/ReportPage.tsx`
- Delete: all files listed in the DELETE section of the file map above

**Interfaces:**
- Produces: all types consumed by every subsequent task — `StatusResponse`, `Artifact`, `AnalysisReport`, `ReportFinding`, `JobListItem`, `JobsListResponse`, `AnalysisRequest`, `ArtifactMessage`, `PlannerArtifact`, `ReviewerArtifact`, `DiscoveryArtifact`, `CollectorArtifact`, `CorrelatorArtifact`, `ReportArtifact`, `JobResult`, `Hypothesis`, `RiskFinding`, `GraphInfo`, `JobStatus`, `Severity`

- [ ] **Step 1: Replace `src/api/types.ts`**

```typescript
// src/api/types.ts
export type JobStatus =
  | 'pending'
  | 'running'
  | 'processing'
  | 'awaiting_approval'
  | 'done'
  | 'failed'
  | 'cancelled'

export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'

export interface AnalysisRequest {
  repo_url: string
  concern: string
}

export interface JobMetadata {
  repo_url: string
  concern: string
}

export interface JobListItem {
  trace_id: string
  status: JobStatus
  concern: string
  created_at: string
  completed_at: string | null
}

export interface JobsListResponse {
  items: JobListItem[]
  total: number
  page: number
  limit: number
  pages: number
}

export interface ProjectMetadata {
  name: string
  package_manager: string
  direct_dependencies_count: number
  transitive_dependencies_count: number
}

export interface DiscoveryResult {
  project_metadata: ProjectMetadata | null
  manifest_files: string[] | null
  discovery_summary: string | null
  discovery_error: string | null
  sbom_result_id: string | null
  sbom_error: string | null
}

export interface Hypothesis {
  id: string
  dep_name: string
  statement: string
  risk_theme: string
  rationale: string
  skills: string[]
  status: 'open' | 'supported' | 'refuted' | 'inconclusive'
  confidence: number | null
}

export interface ContradictionReport {
  evidence_ids: string[]
  description: string
  resolution: string
  adjusted_confidence: number
}

export interface ReportFinding {
  dep_name: string
  risk_score: number
  confidence: number
  severity: Severity
  summary: string
  recommendation: string | null
  alternatives: string[]
  supporting_evidence_count: number
  contradictions_count: number
  missing_evidence: string[]
}

export interface RiskFinding extends ReportFinding {
  hypotheses: Hypothesis[]
  supporting_evidence: string[]
  contradictions: ContradictionReport[]
}

export interface AnalysisReport {
  concern: string
  generated_at: string
  overall_risk_level: Severity | 'none'
  summary: {
    total_deps: number
    critical: number
    high: number
    medium: number
    low: number
  }
  findings: ReportFinding[]
  recommendations: string[]
  contradictions: { description: string; resolution: string }[]
}

export interface JobResult {
  discovery: DiscoveryResult
  risk_findings: RiskFinding[]
  analysis_report: AnalysisReport | null
  review_approved: boolean | null
  review_iterations: number | null
}

// ── Artifacts ──────────────────────────────────────────────────────────────────

export type ArtifactStatus = 'running' | 'done' | 'failed' | 'cancelled'

export interface ArtifactMessage {
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

export interface DiscoveryArtifact extends BaseArtifact {
  node: 'discovery'
  steps: string[]
}

export interface PlanData {
  rationale: string
  hypotheses: Hypothesis[]
  dep_filter: string[] | null
}

export interface PlannerArtifact extends BaseArtifact {
  node: 'investigation_planner'
  data?: { plan: PlanData }
  messages: ArtifactMessage[]
}

export interface CollectorArtifact extends BaseArtifact {
  node: 'evidence_collector'
  steps: string[]
}

export interface CorrelatorData {
  findings_count: number
  contradictions_count: number
  deps_covered: string[]
}

export interface CorrelatorArtifact extends BaseArtifact {
  node: 'evidence_correlator'
  data?: CorrelatorData
}

export interface ReviewerArtifact extends BaseArtifact {
  node: 'finding_reviewer'
  data?: { risk_findings: RiskFinding[] }
  output?: { review_approved: boolean; reviewer_feedback: string | null }
  messages: ArtifactMessage[]
}

export interface ReportArtifact extends BaseArtifact {
  node: 'report_builder'
  output?: AnalysisReport
}

export type Artifact =
  | DiscoveryArtifact
  | PlannerArtifact
  | CollectorArtifact
  | CorrelatorArtifact
  | ReviewerArtifact
  | ReportArtifact

// ── Graph ──────────────────────────────────────────────────────────────────────

export interface GraphNodeInfo {
  id: string
  type: 'terminal' | 'backbone' | 'subgraph'
  order: number
}

export interface GraphEdgeInfo {
  source: string
  target: string
}

export interface GraphInfo {
  nodes: GraphNodeInfo[]
  edges: GraphEdgeInfo[]
}

// ── API responses ─────────────────────────────────────────────────────────────

export interface StatusResponse {
  trace_id: string
  status: JobStatus
  metadata: JobMetadata
  completed_at: string | null
  results: JobResult | null
  artifacts: Artifact[]
  graph: GraphInfo
}

export interface SubmitResponse {
  trace_id: string
  status: JobStatus
}
```

- [ ] **Step 2: Replace `src/api/analyze.ts`**

```typescript
// src/api/analyze.ts
import { apiClient } from './client'
import type {
  AnalysisRequest,
  StatusResponse,
  SubmitResponse,
} from './types'

export async function submitAnalysis(req: AnalysisRequest): Promise<SubmitResponse> {
  return apiClient.post<SubmitResponse>('/analyze', req)
}

export async function getAnalysisStatus(traceId: string): Promise<StatusResponse> {
  return apiClient.get<StatusResponse>(`/analyze/${traceId}`)
}

export async function sendChatMessage(traceId: string, message: string): Promise<void> {
  return apiClient.post(`/analyze/${traceId}/chat`, { message })
}
```

- [ ] **Step 3: Create stub pages**

```typescript
// src/pages/NewAnalysisPage.tsx
export default function NewAnalysisPage() {
  return <main className="py-12 text-center font-mono text-sm text-[--color-muted]">New analysis — coming soon</main>
}
```

```typescript
// src/pages/ExecutionPage.tsx
export default function ExecutionPage() {
  return <main className="py-12 text-center font-mono text-sm text-[--color-muted]">Execution — coming soon</main>
}
```

```typescript
// src/pages/ReportPage.tsx
export default function ReportPage() {
  return <main className="py-12 text-center font-mono text-sm text-[--color-muted]">Report — coming soon</main>
}
```

- [ ] **Step 4: Update `src/App.tsx`**

```typescript
// src/App.tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { PageWrapper } from './components/layout/PageWrapper'
import { Header } from './components/layout/Header'
import { LandingPage } from './pages/LandingPage'
import NewAnalysisPage from './pages/NewAnalysisPage'
import JobsListPage from './pages/JobsListPage'
import ExecutionPage from './pages/ExecutionPage'
import ReportPage from './pages/ReportPage'

export function App() {
  return (
    <BrowserRouter>
      <PageWrapper>
        <Header />
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/new" element={<NewAnalysisPage />} />
          <Route path="/jobs" element={<JobsListPage />} />
          <Route path="/jobs/:traceId" element={<ExecutionPage />} />
          <Route path="/jobs/:traceId/report" element={<ReportPage />} />
        </Routes>
      </PageWrapper>
    </BrowserRouter>
  )
}
```

- [ ] **Step 5: Update `src/components/layout/Header.tsx`**

Read the current Header and replace only the nav link for "scan" → "/new". Keep all other markup identical. The link text should read "New analysis" and point to `/new`.

- [ ] **Step 6: Delete dead files**

```bash
cd apps/frontend
rm src/pages/ScanPage.tsx
rm src/pages/PlanPage.tsx
rm src/pages/JobDetailPage.tsx
rm src/hooks/useAnalysis.ts
rm src/components/analysis/ScanModal.tsx
rm src/components/analysis/AnalysisForm.tsx
rm src/components/analysis/AnalysisResult.tsx
rm src/components/analysis/AnalysisStatus.tsx
rm src/components/analysis/PlanApproval.tsx
rm src/components/analysis/DependencyTree.tsx
rm src/components/graph/panels/SubgraphPanel.tsx
rm src/components/graph/panels/FinalReportPanel.tsx
```

- [ ] **Step 7: Run type-check**

```bash
cd apps/frontend && pnpm type-check
```

Expected: 0 errors. If `client.ts` complains about `StatusResponse` shape mismatch, it is fine — the types are now correct and `client.ts` just uses a generic `get<T>` call, no structural dependency.

- [ ] **Step 8: Commit**

```bash
git add apps/frontend/src
git commit -m "refactor(frontend): replace type system, API client, routing scaffold; delete dead files"
```

---

## Task 2: Utilities and Hooks

Build the four hooks and the `getActiveGate` utility. No UI. These are consumed by the page components in later tasks.

**Files:**
- Create: `src/lib/getActiveGate.ts`
- Create: `src/hooks/useJobStatus.ts`
- Create: `src/hooks/useJobSubmit.ts`
- Create: `src/hooks/useChat.ts`
- Create: `src/hooks/useReport.ts`
- Replace: `src/data/samples.ts`

**Interfaces:**
- Consumes: `Artifact`, `PlannerArtifact`, `ReviewerArtifact`, `StatusResponse`, `AnalysisRequest`, `ArtifactMessage` from `src/api/types.ts`; `usePolling` from `src/hooks/usePolling.ts`; `submitAnalysis`, `getAnalysisStatus`, `sendChatMessage` from `src/api/analyze.ts`
- Produces:
  - `getActiveGate(artifacts: Artifact[]): 'investigation_planner' | 'finding_reviewer' | null`
  - `useJobStatus(traceId: string | undefined): { data: StatusResponse | null; isPolling: boolean; error: Error | null; startPolling: () => void; resume: () => void }`
  - `useJobSubmit(): { submit: (req: AnalysisRequest) => Promise<void>; isSubmitting: boolean; error: Error | null }`
  - `useChat(traceId: string | undefined, artifacts: Artifact[], opts: { autopilot: boolean; onSent: () => void }): { activeGate: Gate | null; messages: ArtifactMessage[]; send: (msg: string) => Promise<void>; isSending: boolean }`
  - `useReport(traceId: string | undefined): { report: AnalysisReport | null; isLoading: boolean; error: Error | null }`
  - `SAMPLES: Sample[]` where `Sample = { id, label, description, repo_url, concern }`

- [ ] **Step 1: Create `src/lib/getActiveGate.ts`**

```typescript
// src/lib/getActiveGate.ts
import type { Artifact, PlannerArtifact, ReviewerArtifact } from '../api/types'

export type Gate = 'investigation_planner' | 'finding_reviewer'

export function getActiveGate(artifacts: Artifact[]): Gate | null {
  const gates: Gate[] = ['investigation_planner', 'finding_reviewer']
  for (const node of gates) {
    const artifact = artifacts.find((a) => a.node === node) as
      | PlannerArtifact
      | ReviewerArtifact
      | undefined
    if (artifact?.status === 'running' && artifact.messages.length > 0) {
      return node
    }
  }
  return null
}
```

- [ ] **Step 2: Create `src/hooks/useJobStatus.ts`**

```typescript
// src/hooks/useJobStatus.ts
import { useCallback } from 'react'
import { getAnalysisStatus } from '../api/analyze'
import { usePolling } from './usePolling'
import type { StatusResponse } from '../api/types'

export function useJobStatus(traceId: string | undefined) {
  const pollFn = useCallback((): Promise<StatusResponse> => {
    if (!traceId) return Promise.reject(new Error('No trace ID'))
    return getAnalysisStatus(traceId)
  }, [traceId])

  const shouldStop = useCallback((data: StatusResponse): boolean => {
    return (
      data.status === 'done' ||
      data.status === 'failed' ||
      data.status === 'cancelled' ||
      data.status === 'awaiting_approval'
    )
  }, [])

  const { data, error, isPolling, startPolling, resumePolling } = usePolling<StatusResponse>(
    pollFn,
    2000,
    shouldStop,
  )

  return { data, error, isPolling, startPolling, resume: resumePolling }
}
```

- [ ] **Step 3: Create `src/hooks/useJobSubmit.ts`**

```typescript
// src/hooks/useJobSubmit.ts
import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { submitAnalysis } from '../api/analyze'
import type { AnalysisRequest } from '../api/types'

export function useJobSubmit() {
  const navigate = useNavigate()
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const submit = useCallback(
    async (req: AnalysisRequest) => {
      setIsSubmitting(true)
      setError(null)
      try {
        const res = await submitAnalysis(req)
        navigate(`/jobs/${res.trace_id}`)
      } catch (err) {
        setError(err instanceof Error ? err : new Error(String(err)))
      } finally {
        setIsSubmitting(false)
      }
    },
    [navigate],
  )

  return { submit, isSubmitting, error }
}
```

- [ ] **Step 4: Create `src/hooks/useChat.ts`**

```typescript
// src/hooks/useChat.ts
import { useCallback, useEffect, useRef, useState } from 'react'
import { sendChatMessage } from '../api/analyze'
import { getActiveGate } from '../lib/getActiveGate'
import type { Artifact, ArtifactMessage, PlannerArtifact, ReviewerArtifact } from '../api/types'
import type { Gate } from '../lib/getActiveGate'

interface UseChatOptions {
  autopilot: boolean
  onSent: () => void
}

export function useChat(
  traceId: string | undefined,
  artifacts: Artifact[],
  opts: UseChatOptions,
) {
  const [isSending, setIsSending] = useState(false)
  const hasFiredRef = useRef<Gate | null>(null)
  const optsRef = useRef(opts)
  useEffect(() => {
    optsRef.current = opts
  })

  const activeGate = getActiveGate(artifacts)

  const messages: ArtifactMessage[] = (() => {
    if (!activeGate) return []
    const artifact = artifacts.find((a) => a.node === activeGate) as
      | PlannerArtifact
      | ReviewerArtifact
      | undefined
    return artifact?.messages ?? []
  })()

  const send = useCallback(
    async (message: string) => {
      if (!traceId) return
      setIsSending(true)
      try {
        await sendChatMessage(traceId, message)
        optsRef.current.onSent()
      } finally {
        setIsSending(false)
      }
    },
    [traceId],
  )

  // Autopilot: auto-approve each gate once
  useEffect(() => {
    if (!optsRef.current.autopilot || !activeGate) return
    if (hasFiredRef.current === activeGate) return
    hasFiredRef.current = activeGate
    void send('Yes, proceed')
  }, [activeGate, send])

  // Reset fired ref when gate closes
  useEffect(() => {
    if (!activeGate) hasFiredRef.current = null
  }, [activeGate])

  return { activeGate, messages, send, isSending }
}
```

- [ ] **Step 5: Create `src/hooks/useReport.ts`**

```typescript
// src/hooks/useReport.ts
import { useEffect, useState } from 'react'
import { getAnalysisStatus } from '../api/analyze'
import type { AnalysisReport } from '../api/types'

export function useReport(traceId: string | undefined) {
  const [report, setReport] = useState<AnalysisReport | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    if (!traceId) return
    setIsLoading(true)
    getAnalysisStatus(traceId)
      .then((data) => {
        setReport(data.results?.analysis_report ?? null)
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err : new Error(String(err)))
      })
      .finally(() => setIsLoading(false))
  }, [traceId])

  return { report, isLoading, error }
}
```

- [ ] **Step 6: Replace `src/data/samples.ts`**

```typescript
// src/data/samples.ts
export interface Sample {
  id: string
  label: string
  description: string
  repo_url: string
  concern: string
}

export const SAMPLES: Sample[] = [
  {
    id: 'supply-chain',
    label: 'Supply chain',
    description: 'Express API — look for malicious postinstall scripts in transitive deps',
    repo_url: 'https://github.com/expressjs/express',
    concern: 'Supply chain attack via malicious postinstall scripts in transitive dependencies',
  },
  {
    id: 'license-risk',
    label: 'License compliance',
    description: 'React project — surface GPL or AGPL licenses that block commercial use',
    repo_url: 'https://github.com/facebook/react',
    concern: 'License compliance — identify any GPL or AGPL dependencies that could affect commercial distribution',
  },
  {
    id: 'known-cves',
    label: 'Known CVEs',
    description: 'Legacy API — assess exploitability of pinned CVE-affected versions',
    repo_url: 'https://github.com/nodejs/node',
    concern: 'Known vulnerabilities (CVEs) in pinned dependency versions — assess exploitability and upgrade urgency',
  },
  {
    id: 'maintainer-trust',
    label: 'Maintainer trust',
    description: 'Check for abandoned or single-maintainer dependencies',
    repo_url: 'https://github.com/vercel/next.js',
    concern: 'Maintainer trust and bus factor — identify dependencies with low activity or single maintainers',
  },
]
```

- [ ] **Step 7: Run type-check**

```bash
cd apps/frontend && pnpm type-check
```

Expected: 0 errors.

- [ ] **Step 8: Commit**

```bash
git add apps/frontend/src
git commit -m "feat(frontend): add hooks (useJobStatus, useJobSubmit, useChat, useReport), getActiveGate utility, updated samples"
```

---

## Task 3: Graph Foundation

Update the graph type system and state mapper to reflect the new 8-node pipeline.

**Files:**
- Replace: `src/components/graph/graphDefinition.ts`
- Replace: `src/components/graph/graphStateMapper.ts`

**Interfaces:**
- Consumes: `Artifact`, `DiscoveryArtifact`, `PlannerArtifact`, `CollectorArtifact`, `ReviewerArtifact`, `ReportArtifact`, `StatusResponse` from `src/api/types.ts`
- Produces:
  - `NodeId` type (10 IDs)
  - `NodeStatus` type (`'idle' | 'active' | 'awaiting' | 'done' | 'failed' | 'cancelled'`)
  - `GraphNodeDef`, `GraphEdgeDef`, `GraphNodeState`, `GraphRenderData` interfaces
  - `buildGraphDef(graph: GraphInfo): { nodes: GraphNodeDef[]; edges: GraphEdgeDef[] }`
  - `mapResponseToGraphState(response: StatusResponse | null): GraphRenderData`

- [ ] **Step 1: Replace `src/components/graph/graphDefinition.ts`**

```typescript
// src/components/graph/graphDefinition.ts
import type { GraphInfo } from '../../api/types'

export type NodeId =
  | 'START'
  | 'discovery'
  | 'investigation_planner'
  | 'skill_dispatcher'
  | 'skill_executor'
  | 'evidence_collector'
  | 'evidence_correlator'
  | 'finding_reviewer'
  | 'report_builder'
  | 'END'

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

// Static fallback backbone (used when API graph is unavailable)
export const GRAPH_NODES: GraphNodeDef[] = [
  { id: 'START',                label: 'START',                 layer: 0, isSubgraph: false },
  { id: 'discovery',            label: 'discovery',             layer: 1, isSubgraph: false },
  { id: 'investigation_planner',label: 'investigation_planner', layer: 2, isSubgraph: false },
  { id: 'skill_dispatcher',     label: 'skill_dispatcher',      layer: 3, isSubgraph: false },
  { id: 'skill_executor',       label: 'skill_executor',        layer: 4, isSubgraph: false },
  { id: 'evidence_collector',   label: 'evidence_collector',    layer: 5, isSubgraph: false },
  { id: 'evidence_correlator',  label: 'evidence_correlator',   layer: 6, isSubgraph: false },
  { id: 'finding_reviewer',     label: 'finding_reviewer',      layer: 7, isSubgraph: false },
  { id: 'report_builder',       label: 'report_builder',        layer: 8, isSubgraph: false },
  { id: 'END',                  label: 'END',                   layer: 9, isSubgraph: false },
]

export const GRAPH_EDGES: GraphEdgeDef[] = [
  { source: 'START',                target: 'discovery' },
  { source: 'discovery',            target: 'investigation_planner' },
  { source: 'investigation_planner',target: 'skill_dispatcher' },
  { source: 'skill_dispatcher',     target: 'skill_executor' },
  { source: 'skill_executor',       target: 'evidence_collector' },
  { source: 'evidence_collector',   target: 'evidence_correlator' },
  { source: 'evidence_correlator',  target: 'finding_reviewer' },
  { source: 'finding_reviewer',     target: 'evidence_correlator' },
  { source: 'finding_reviewer',     target: 'report_builder' },
  { source: 'report_builder',       target: 'END' },
]

export function buildGraphDef(graph: GraphInfo): { nodes: GraphNodeDef[]; edges: GraphEdgeDef[] } {
  const layerCounter = new Map<number, number>()
  const nodes: GraphNodeDef[] = graph.nodes.map((n) => {
    const layer = n.order
    let laneIndex: number | undefined
    if (n.type === 'subgraph') {
      const current = layerCounter.get(layer) ?? 0
      laneIndex = current
      layerCounter.set(layer, current + 1)
    }
    return {
      id: n.id as NodeId,
      label: n.id,
      layer,
      isSubgraph: n.type === 'subgraph',
      laneIndex,
    }
  })
  const edges: GraphEdgeDef[] = graph.edges.map((e) => ({
    source: e.source as NodeId,
    target: e.target as NodeId,
  }))
  return { nodes, edges }
}
```

- [ ] **Step 2: Replace `src/components/graph/graphStateMapper.ts`**

```typescript
// src/components/graph/graphStateMapper.ts
import type {
  StatusResponse,
  Artifact,
  DiscoveryArtifact,
  PlannerArtifact,
  CollectorArtifact,
  ReviewerArtifact,
  ReportArtifact,
} from '../../api/types'
import { GRAPH_NODES, GRAPH_EDGES, buildGraphDef } from './graphDefinition'
import type { GraphRenderData, GraphNodeState, NodeStatus, NodeId } from './graphDefinition'

export function mapResponseToGraphState(response: StatusResponse | null): GraphRenderData {
  if (!response) {
    const nodes = GRAPH_NODES.map(
      (def): GraphNodeState => ({ id: def.id, def, status: 'idle', hasDetail: false }),
    )
    return { nodes, edges: filterEdges(nodes, GRAPH_EDGES) }
  }

  const artifacts = response.artifacts ?? []
  const { nodes: nodeDefs, edges } = response.graph
    ? buildGraphDef(response.graph)
    : { nodes: GRAPH_NODES, edges: GRAPH_EDGES }

  const nodes = nodeDefs.map(
    (def): GraphNodeState => ({
      id: def.id,
      def,
      status: deriveStatus(def.id, response.status, artifacts),
      hasDetail: hasDetail(def.id, artifacts),
    }),
  )

  return { nodes, edges: filterEdges(nodes, edges) }
}

function deriveStatus(id: NodeId, jobStatus: StatusResponse['status'], artifacts: Artifact[]): NodeStatus {
  if (id === 'START') return jobStatus === 'pending' ? 'idle' : 'done'
  if (id === 'END') {
    if (jobStatus === 'done') return 'done'
    if (jobStatus === 'failed') return 'failed'
    return 'idle'
  }

  const artifact = artifacts.find((a) => a.node === id)
  if (artifact) {
    if (artifact.status === 'done') return 'done'
    if (artifact.status === 'failed') return 'failed'
    if (artifact.status === 'cancelled') return 'cancelled'
    if (artifact.status === 'running') {
      if (
        (id === 'investigation_planner' || id === 'finding_reviewer') &&
        'messages' in artifact &&
        (artifact as PlannerArtifact | ReviewerArtifact).messages.length > 0
      ) {
        return 'awaiting'
      }
      return 'active'
    }
  }

  if (jobStatus === 'cancelled') return 'cancelled'
  return 'idle'
}

function hasDetail(id: NodeId, artifacts: Artifact[]): boolean {
  const artifact = artifacts.find((a) => a.node === id)
  if (!artifact) return false
  switch (id) {
    case 'discovery':
      return (artifact as DiscoveryArtifact).steps?.length > 0
    case 'investigation_planner':
      return (artifact as PlannerArtifact).messages.length > 0
    case 'skill_executor': {
      const collector = artifacts.find((a) => a.node === 'evidence_collector') as CollectorArtifact | undefined
      return (collector?.steps?.length ?? 0) > 0
    }
    case 'evidence_correlator':
      return !!(artifact as { data?: unknown }).data
    case 'finding_reviewer':
      return ((artifact as ReviewerArtifact).data?.risk_findings?.length ?? 0) > 0
    case 'report_builder':
      return !!(artifact as ReportArtifact).output
    default:
      return false
  }
}

function filterEdges(
  nodes: GraphNodeState[],
  edges: { source: NodeId; target: NodeId }[],
) {
  const ids = new Set(nodes.map((n) => n.id))
  return edges.filter((e) => ids.has(e.source) && ids.has(e.target))
}
```

- [ ] **Step 3: Run type-check**

```bash
cd apps/frontend && pnpm type-check
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add apps/frontend/src/components/graph/graphDefinition.ts apps/frontend/src/components/graph/graphStateMapper.ts
git commit -m "refactor(graph): update NodeId/NodeStatus types and state mapper for new 8-node pipeline"
```

---

## Task 4: ExecutionGraph Status Colors + NodeDetailPanel Slide-in

Add `awaiting` (amber pulsing border) and `cancelled` (muted grey) rendering to the D3 graph. Update `NodeDetailPanel` to behave as a slide-in overlay.

**Files:**
- Modify: `src/components/graph/ExecutionGraph.tsx`
- Modify: `src/components/graph/NodeDetailPanel.tsx`

**Interfaces:**
- Consumes: `NodeStatus` (now includes `'awaiting'` and `'cancelled'`) from `graphDefinition.ts`
- Produces: same `ExecutionGraph` and `NodeDetailPanel` component APIs — no prop changes

- [ ] **Step 1: Update status color helpers in `ExecutionGraph.tsx`**

Find the `statusStrokeColor` and `statusDotFill` functions and replace them with:

```typescript
function statusStrokeColor(
  status: NodeStatus,
  colors: { border: string; accent: string; done: string; error: string; running: string; awaiting: string; cancelled: string },
): string {
  switch (status) {
    case 'active':    return colors.running
    case 'awaiting':  return colors.awaiting
    case 'done':      return colors.done
    case 'failed':    return colors.error
    case 'cancelled': return colors.cancelled
    default:          return colors.border
  }
}

function statusDotFill(
  status: NodeStatus,
  colors: { accent: string; done: string; error: string; running: string; awaiting: string },
): string {
  switch (status) {
    case 'active':   return colors.running
    case 'awaiting': return colors.awaiting
    case 'done':     return colors.done
    case 'failed':   return colors.error
    default:         return 'transparent'
  }
}
```

- [ ] **Step 2: Extend the colors object inside the `useEffect` in `ExecutionGraph.tsx`**

Find the `const colors = { ... }` block and add two new entries:

```typescript
const colors = {
  border:    cssVar(container, '--color-border'),
  text:      cssVar(container, '--color-text'),
  muted:     cssVar(container, '--color-muted'),
  accent:    cssVar(container, '--color-accent'),
  surface:   cssVar(container, '--color-surface-raised'),
  error:     cssVar(container, '--color-error'),
  done:      '#34c785',
  running:   '#eab308',
  awaiting:  '#f5a623',   // amber — same hue as accent, used for pulsing border
  cancelled: '#3f4152',   // muted dark grey
}
```

- [ ] **Step 3: Add `awaiting` pulse animation to the defs block**

Inside the `defs.append('style').text(...)` call, append the new keyframe:

```typescript
defs
  .append('style')
  .text(
    '@keyframes nodePulse { 0%,100%{opacity:1} 50%{opacity:0.3} }' +
      '.node-pulse { animation: nodePulse 1.8s ease-in-out infinite; }' +
      '@keyframes borderPulse { 0%,100%{stroke-opacity:1} 50%{stroke-opacity:0.3} }' +
      '.node-awaiting { animation: borderPulse 1.4s ease-in-out infinite; }' +
      '@keyframes edgeFlow { from { stroke-dashoffset: 20 } to { stroke-dashoffset: 0 } }' +
      '.edge-flow { animation: edgeFlow 0.6s linear infinite; }',
  )
```

- [ ] **Step 4: Apply `node-awaiting` class to rect when status is `awaiting`**

After the `g.append('rect')...` call for non-terminal nodes, add:

```typescript
if (node.status === 'awaiting') {
  g.select('rect').attr('class', 'node-awaiting')
}
```

Also update the dot visibility: add `awaiting` to the conditions that show the dot (after the existing `active` branch):

```typescript
if (node.status === 'idle') {
  dot.attr('opacity', 0)
} else if (node.status === 'active') {
  dot.attr('class', 'node-pulse')
} else if (node.status === 'awaiting') {
  dot.attr('class', 'node-pulse')  // also pulse
} else if (node.status === 'cancelled') {
  dot.attr('opacity', 0.3)
}
```

- [ ] **Step 5: Replace `src/components/graph/NodeDetailPanel.tsx`**

The panel now slides in from the right as a fixed overlay, not a block below the graph.

```typescript
// src/components/graph/NodeDetailPanel.tsx
import { useEffect } from 'react'
import type { ArtifactInfo } from '../../api/types'  // keep backward compat alias
import type { NodeId } from './graphDefinition'
import { getPanelComponent } from './nodeRegistry'
import type { Artifact, JobResult } from '../../api/types'

interface NodeDetailPanelProps {
  nodeId: NodeId | null
  results: JobResult | null
  artifacts: Artifact[]
  onClose: () => void
}

export function NodeDetailPanel({ nodeId, results, artifacts, onClose }: NodeDetailPanelProps) {
  // Close on Escape key
  useEffect(() => {
    if (!nodeId) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [nodeId, onClose])

  if (!nodeId) return null

  const Panel = getPanelComponent(nodeId)

  return (
    <>
      {/* Transparent backdrop — clicking outside closes */}
      <div className="fixed inset-0 z-20" onClick={onClose} aria-hidden="true" />

      {/* Slide-in panel */}
      <div className="fixed top-0 right-0 bottom-0 z-30 flex w-full max-w-md flex-col border-l border-[--color-border] bg-[--color-surface] shadow-2xl">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-[--color-border] px-5 py-4">
          <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
            node / {nodeId}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="font-mono text-xs text-[--color-muted] transition-colors hover:text-[--color-text]"
            aria-label="Close panel"
          >
            ✕
          </button>
        </div>

        {/* Panel content */}
        <div className="flex-1 overflow-y-auto p-5">
          {Panel ? (
            <Panel nodeId={nodeId} results={results} artifacts={artifacts} />
          ) : (
            <p className="font-mono text-xs text-[--color-muted]">No detail available for this node.</p>
          )}
        </div>
      </div>
    </>
  )
}
```

- [ ] **Step 6: Update `PanelProps` interface** (in `src/components/graph/panels/DiscoveryPanel.tsx` since it's the canonical source)

```typescript
// PanelProps — used by all panel components
export interface PanelProps {
  nodeId: NodeId
  results: JobResult | null
  artifacts: Artifact[]
}
```

- [ ] **Step 7: Run type-check**

```bash
cd apps/frontend && pnpm type-check
```

Expected: 0 errors. The existing `DiscoveryPanel` and `PlannerPanel` may warn on the changed `results` type — that is expected and will be fixed in Task 5.

- [ ] **Step 8: Commit**

```bash
git add apps/frontend/src/components/graph/ExecutionGraph.tsx apps/frontend/src/components/graph/NodeDetailPanel.tsx apps/frontend/src/components/graph/panels/DiscoveryPanel.tsx
git commit -m "feat(graph): add awaiting/cancelled status colors, slide-in NodeDetailPanel"
```

---

## Task 5: Node Panels + Registry

Rewrite all node panels to consume the new artifact and result shapes. Update the registry.

**Files:**
- Replace: `src/components/graph/panels/DiscoveryPanel.tsx`
- Replace: `src/components/graph/panels/PlannerPanel.tsx`
- Create: `src/components/graph/panels/SkillExecutorPanel.tsx`
- Create: `src/components/graph/panels/CorrelatorPanel.tsx`
- Create: `src/components/graph/panels/FindingReviewerPanel.tsx`
- Create: `src/components/graph/panels/ReportBuilderPanel.tsx`
- Replace: `src/components/graph/nodeRegistry.ts`

**Interfaces:**
- Consumes: `PanelProps` (from DiscoveryPanel); `DiscoveryArtifact`, `PlannerArtifact`, `CollectorArtifact`, `CorrelatorArtifact`, `ReviewerArtifact`, `ReportArtifact`, `JobResult`, `Artifact` from `src/api/types.ts`
- Produces: `getPanelComponent(id: NodeId): PanelComponent | undefined`

- [ ] **Step 1: Replace `src/components/graph/panels/DiscoveryPanel.tsx`**

```typescript
// src/components/graph/panels/DiscoveryPanel.tsx
import type { Artifact, JobResult } from '../../../api/types'
import type { NodeId } from '../graphDefinition'
import type { DiscoveryArtifact } from '../../../api/types'

export interface PanelProps {
  nodeId: NodeId
  results: JobResult | null
  artifacts: Artifact[]
}

export function DiscoveryPanel({ results, artifacts }: PanelProps) {
  const artifact = artifacts.find((a) => a.node === 'discovery') as DiscoveryArtifact | undefined
  const meta = results?.discovery?.project_metadata
  const summary = results?.discovery?.discovery_summary
  const error = results?.discovery?.discovery_error
  const steps = artifact?.steps ?? []

  return (
    <div className="space-y-5">
      {/* Dep counts */}
      {meta && (
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1 rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
            <p className="font-mono text-2xl font-semibold text-[--color-text]">
              {meta.direct_dependencies_count}
            </p>
            <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Direct</p>
          </div>
          <div className="space-y-1 rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
            <p className="font-mono text-2xl font-semibold text-[--color-text]">
              {meta.transitive_dependencies_count}
            </p>
            <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Transitive</p>
          </div>
        </div>
      )}

      {/* Project metadata */}
      {meta && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 font-mono text-xs">
          <dt className="tracking-widest text-[--color-muted] uppercase">Name</dt>
          <dd className="text-[--color-text]">{meta.name}</dd>
          <dt className="tracking-widest text-[--color-muted] uppercase">Manager</dt>
          <dd className="text-[--color-text]">{meta.package_manager}</dd>
        </dl>
      )}

      {/* Pipeline steps */}
      {steps.length > 0 && (
        <div className="space-y-1">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Steps</p>
          <ul className="space-y-1">
            {steps.map((step) => (
              <li key={step} className="flex items-center gap-2 font-mono text-xs text-[--color-text]">
                <span className="text-[--color-done] text-xs">✓</span>
                {step}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Summary */}
      {summary && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Summary</p>
          <div className="rounded border border-[--color-border] bg-[--color-surface-raised] px-4 py-3">
            <p className="font-mono text-xs leading-relaxed text-[--color-text]">{summary}</p>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded border border-[--color-error]/30 bg-[--color-error]/5 px-4 py-3">
          <p className="font-mono text-xs text-[--color-error]">
            <span className="font-semibold">Error: </span>{error}
          </p>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Replace `src/components/graph/panels/PlannerPanel.tsx`**

```typescript
// src/components/graph/panels/PlannerPanel.tsx
import type { PanelProps } from './DiscoveryPanel'
import type { PlannerArtifact } from '../../../api/types'

export function PlannerPanel({ artifacts }: PanelProps) {
  const artifact = artifacts.find((a) => a.node === 'investigation_planner') as
    | PlannerArtifact
    | undefined
  const messages = artifact?.messages ?? []
  const plan = artifact?.data?.plan

  return (
    <div className="space-y-5">
      {/* Chat transcript */}
      {messages.length > 0 && (
        <div className="space-y-3">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Conversation
          </p>
          {messages.map((msg, i) => (
            <div
              key={i}
              className={
                msg.role === 'human'
                  ? 'flex justify-end'
                  : ''
              }
            >
              <div
                className={[
                  'max-w-[85%] rounded border px-3 py-2',
                  msg.role === 'human'
                    ? 'border-[--color-border] bg-[--color-surface]'
                    : 'border-[--color-border] bg-[--color-surface-raised]',
                ].join(' ')}
              >
                <p className="font-mono text-xs leading-relaxed whitespace-pre-wrap text-[--color-text]">
                  {msg.content}
                </p>
                {msg.action && (
                  <p className="mt-1 font-mono text-[10px] text-[--color-accent]">
                    action: {msg.action}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Hypothesis list */}
      {plan && plan.hypotheses.length > 0 && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Hypotheses ({plan.hypotheses.length})
          </p>
          <ul className="space-y-2">
            {plan.hypotheses.map((h) => (
              <li
                key={h.id}
                className="rounded border border-[--color-border] bg-[--color-surface-raised] p-3"
              >
                <p className="mb-1 font-mono text-xs font-semibold text-[--color-text]">
                  {h.dep_name}
                </p>
                <p className="mb-2 font-mono text-xs leading-relaxed text-[--color-muted]">
                  {h.statement}
                </p>
                <div className="flex flex-wrap gap-1">
                  {h.skills.map((s) => (
                    <span
                      key={s}
                      className="inline-flex items-center rounded border border-[--color-border] bg-[--color-surface] px-2 py-0.5 font-mono text-[10px] text-[--color-muted]"
                    >
                      {s}
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!artifact && (
        <p className="font-mono text-xs text-[--color-muted]">No plan data yet.</p>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Create `src/components/graph/panels/SkillExecutorPanel.tsx`**

```typescript
// src/components/graph/panels/SkillExecutorPanel.tsx
import type { PanelProps } from './DiscoveryPanel'
import type { CollectorArtifact } from '../../../api/types'

export function SkillExecutorPanel({ artifacts }: PanelProps) {
  const collector = artifacts.find((a) => a.node === 'evidence_collector') as
    | CollectorArtifact
    | undefined
  const steps = collector?.steps ?? []

  if (steps.length === 0) {
    return <p className="font-mono text-xs text-[--color-muted]">No skills executed yet.</p>
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
          Skills executed
        </p>
        <span className="rounded-full border border-[--color-border] bg-[--color-surface-raised] px-2 py-0.5 font-mono text-[10px] text-[--color-text]">
          {steps.length}
        </span>
      </div>
      <ul className="space-y-1">
        {steps.map((step) => (
          <li key={step} className="flex items-center gap-2 font-mono text-xs text-[--color-text]">
            <span className="text-[10px] text-[--color-accent]">▸</span>
            {step}
          </li>
        ))}
      </ul>
    </div>
  )
}
```

- [ ] **Step 4: Create `src/components/graph/panels/CorrelatorPanel.tsx`**

```typescript
// src/components/graph/panels/CorrelatorPanel.tsx
import type { PanelProps } from './DiscoveryPanel'
import type { CorrelatorArtifact } from '../../../api/types'

export function CorrelatorPanel({ artifacts }: PanelProps) {
  const artifact = artifacts.find((a) => a.node === 'evidence_correlator') as
    | CorrelatorArtifact
    | undefined
  const data = artifact?.data

  if (!data) {
    return <p className="font-mono text-xs text-[--color-muted]">Correlation not yet complete.</p>
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
          <p className="font-mono text-2xl font-semibold text-[--color-text]">{data.findings_count}</p>
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Findings</p>
        </div>
        <div className="rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
          <p className="font-mono text-2xl font-semibold text-[--color-text]">{data.contradictions_count}</p>
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Contradictions</p>
        </div>
      </div>

      {data.deps_covered.length > 0 && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Dependencies covered
          </p>
          <div className="flex flex-wrap gap-1.5">
            {data.deps_covered.map((dep) => (
              <span
                key={dep}
                className="inline-flex items-center rounded border border-[--color-border] bg-[--color-surface-raised] px-2 py-0.5 font-mono text-[10px] text-[--color-text]"
              >
                {dep}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Create `src/components/graph/panels/FindingReviewerPanel.tsx`**

```typescript
// src/components/graph/panels/FindingReviewerPanel.tsx
import type { PanelProps } from './DiscoveryPanel'
import type { ReviewerArtifact } from '../../../api/types'

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'text-red-400 border-red-500/40 bg-red-500/5',
  high:     'text-orange-400 border-orange-500/40 bg-orange-500/5',
  medium:   'text-yellow-400 border-yellow-500/40 bg-yellow-500/5',
  low:      'text-blue-400 border-blue-500/40 bg-blue-500/5',
  info:     'text-[--color-muted] border-[--color-border] bg-[--color-surface-raised]',
}

export function FindingReviewerPanel({ artifacts }: PanelProps) {
  const artifact = artifacts.find((a) => a.node === 'finding_reviewer') as
    | ReviewerArtifact
    | undefined
  const findings = artifact?.data?.risk_findings ?? []
  const messages = artifact?.messages ?? []

  return (
    <div className="space-y-5">
      {findings.length > 0 && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Findings ({findings.length})
          </p>
          <ul className="space-y-2">
            {findings.map((f) => (
              <li
                key={f.dep_name}
                className="flex items-start gap-3 rounded border border-[--color-border] bg-[--color-surface-raised] p-3"
              >
                <span
                  className={[
                    'mt-0.5 shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase',
                    SEVERITY_COLORS[f.severity] ?? SEVERITY_COLORS.info,
                  ].join(' ')}
                >
                  {f.severity}
                </span>
                <div className="min-w-0">
                  <p className="font-mono text-xs font-semibold text-[--color-text]">{f.dep_name}</p>
                  <p className="font-mono text-[10px] text-[--color-muted]">
                    score: {f.risk_score.toFixed(1)}/10
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {messages.length > 0 && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Review conversation
          </p>
          {messages.map((msg, i) => (
            <div key={i} className={msg.role === 'human' ? 'flex justify-end' : ''}>
              <div className="max-w-[85%] rounded border border-[--color-border] bg-[--color-surface-raised] px-3 py-2">
                <p className="font-mono text-xs leading-relaxed whitespace-pre-wrap text-[--color-text]">
                  {msg.content}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {!artifact && (
        <p className="font-mono text-xs text-[--color-muted]">No review data yet.</p>
      )}
    </div>
  )
}
```

- [ ] **Step 6: Create `src/components/graph/panels/ReportBuilderPanel.tsx`**

```typescript
// src/components/graph/panels/ReportBuilderPanel.tsx
import { Link, useParams } from 'react-router-dom'
import type { PanelProps } from './DiscoveryPanel'
import type { ReportArtifact } from '../../../api/types'

export function ReportBuilderPanel({ artifacts }: PanelProps) {
  const { traceId } = useParams<{ traceId: string }>()
  const artifact = artifacts.find((a) => a.node === 'report_builder') as ReportArtifact | undefined
  const report = artifact?.output

  if (!report) {
    return <p className="font-mono text-xs text-[--color-muted]">Report not yet available.</p>
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
        <p className="mb-1 font-mono text-xs tracking-widest text-[--color-muted] uppercase">
          Overall risk
        </p>
        <p className="font-mono text-lg font-semibold uppercase text-[--color-text]">
          {report.overall_risk_level}
        </p>
        <p className="mt-1 font-mono text-xs text-[--color-muted]">
          {report.summary.total_deps} dependencies analysed
        </p>
      </div>

      {traceId && (
        <Link
          to={`/jobs/${traceId}/report`}
          className="block w-full rounded-lg border border-[--color-accent]/40 bg-[--color-accent]/5 px-4 py-3 text-center font-mono text-xs font-semibold text-[--color-accent] transition-colors hover:bg-[--color-accent]/10"
        >
          View full report →
        </Link>
      )}
    </div>
  )
}
```

- [ ] **Step 7: Replace `src/components/graph/nodeRegistry.ts`**

```typescript
// src/components/graph/nodeRegistry.ts
import type { ComponentType } from 'react'
import type { NodeId } from './graphDefinition'
import type { PanelProps } from './panels/DiscoveryPanel'
import { DiscoveryPanel } from './panels/DiscoveryPanel'
import { PlannerPanel } from './panels/PlannerPanel'
import { SkillExecutorPanel } from './panels/SkillExecutorPanel'
import { CorrelatorPanel } from './panels/CorrelatorPanel'
import { FindingReviewerPanel } from './panels/FindingReviewerPanel'
import { ReportBuilderPanel } from './panels/ReportBuilderPanel'

export type { PanelProps }
type PanelComponent = ComponentType<PanelProps>

export const NODE_PANEL_REGISTRY = new Map<NodeId, PanelComponent>([
  ['discovery',             DiscoveryPanel],
  ['investigation_planner', PlannerPanel],
  ['skill_executor',        SkillExecutorPanel],
  ['evidence_correlator',   CorrelatorPanel],
  ['finding_reviewer',      FindingReviewerPanel],
  ['report_builder',        ReportBuilderPanel],
])

export function getPanelComponent(id: NodeId): PanelComponent | undefined {
  return NODE_PANEL_REGISTRY.get(id)
}
```

- [ ] **Step 8: Run type-check**

```bash
cd apps/frontend && pnpm type-check
```

Expected: 0 errors.

- [ ] **Step 9: Commit**

```bash
git add apps/frontend/src/components/graph/
git commit -m "feat(panels): add SkillExecutorPanel, CorrelatorPanel, FindingReviewerPanel, ReportBuilderPanel; rewrite DiscoveryPanel, PlannerPanel; update nodeRegistry"
```

---

## Task 6: Chat Overlay

Install `marked` and build the `ChatOverlay` component used for both HITL gates.

**Files:**
- Install: `marked`
- Create: `src/components/chat/ChatOverlay.tsx`

**Interfaces:**
- Consumes: `ArtifactMessage` from `src/api/types.ts`; `Gate` from `src/lib/getActiveGate.ts`
- Produces:
  ```typescript
  interface ChatOverlayProps {
    open: boolean
    onClose: () => void
    activeGate: Gate | null
    messages: ArtifactMessage[]
    isSending: boolean
    onSend: (message: string) => Promise<void>
  }
  ```

- [ ] **Step 1: Install `marked`**

```bash
cd apps/frontend && pnpm add marked
```

- [ ] **Step 2: Create `src/components/chat/ChatOverlay.tsx`**

```typescript
// src/components/chat/ChatOverlay.tsx
import { useEffect, useRef, useState } from 'react'
import { marked } from 'marked'
import { cn } from '../../lib/utils'
import { Button } from '../ui/Button'
import { Spinner } from '../ui/Spinner'
import type { ArtifactMessage } from '../../api/types'
import type { Gate } from '../../lib/getActiveGate'

interface ChatOverlayProps {
  open: boolean
  onClose: () => void
  activeGate: Gate | null
  messages: ArtifactMessage[]
  isSending: boolean
  onSend: (message: string) => Promise<void>
}

const GATE_LABELS: Record<Gate, string> = {
  investigation_planner: 'Investigation Plan Review',
  finding_reviewer:      'Risk Findings Review',
}

export function ChatOverlay({
  open,
  onClose,
  activeGate,
  messages,
  isSending,
  onSend,
}: ChatOverlayProps) {
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Close on Escape when dismissible
  useEffect(() => {
    if (!open || activeGate) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, activeGate, onClose])

  if (!open) return null

  const isDismissible = !activeGate
  const headerLabel = activeGate ? GATE_LABELS[activeGate] : 'Conversation History'
  const showQuickActions = activeGate === 'investigation_planner'

  async function handleSend(text: string) {
    const trimmed = text.trim()
    if (!trimmed || isSending) return
    setInput('')
    await onSend(trimmed)
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={isDismissible ? onClose : undefined}
        aria-hidden="true"
      />

      {/* Panel */}
      <div className="relative z-10 flex h-[70vh] w-full max-w-xl flex-col rounded-xl border border-[--color-border] bg-[--color-surface] shadow-2xl">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-[--color-border] px-5 py-4">
          <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
            {headerLabel}
          </span>
          {isDismissible && (
            <button
              type="button"
              onClick={onClose}
              className="font-mono text-xs text-[--color-muted] transition-colors hover:text-[--color-text]"
              aria-label="Close"
            >
              ✕
            </button>
          )}
        </div>

        {/* Messages */}
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {messages.length === 0 && (
            <p className="py-8 text-center font-mono text-xs text-[--color-muted]">
              Waiting for agent…
            </p>
          )}
          {messages.map((msg, i) => {
            if (msg.role === 'human') {
              return (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[80%] rounded-lg border border-[--color-border] bg-[--color-surface-raised] px-4 py-3">
                    <p className="font-mono text-xs text-[--color-text]">{msg.content}</p>
                  </div>
                </div>
              )
            }
            const html = marked.parse(msg.content) as string
            return (
              <div key={i} className="rounded-lg border border-[--color-border] bg-[--color-surface-raised] px-4 py-3">
                <div
                  className="prose prose-invert prose-sm max-w-none font-mono text-xs leading-relaxed text-[--color-text] [&_strong]:text-[--color-text] [&_li]:text-[--color-muted]"
                  // eslint-disable-next-line react/no-danger
                  dangerouslySetInnerHTML={{ __html: html }}
                />
              </div>
            )
          })}
          {isSending && (
            <div className="flex items-center gap-2 font-mono text-xs text-[--color-muted]">
              <Spinner size="sm" />
              Sending…
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Quick actions — Gate 1 only */}
        {showQuickActions && (
          <div className="shrink-0 flex flex-wrap items-center gap-2 border-t border-[--color-border] px-5 py-3">
            <span className="font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
              quick:
            </span>
            <button
              type="button"
              disabled={isSending}
              onClick={() => void handleSend('Yes, proceed with the plan')}
              className="rounded border border-[--badge-done-border] px-2.5 py-1 font-mono text-[10px] text-[--badge-done-text] transition-colors hover:bg-[--badge-done-bg] disabled:opacity-40"
            >
              Yes, proceed
            </button>
            <button
              type="button"
              disabled={isSending}
              onClick={() => void handleSend('Cancel this analysis')}
              className="rounded border border-[--badge-failed-border] px-2.5 py-1 font-mono text-[10px] text-[--badge-failed-text] transition-colors hover:bg-[--badge-failed-bg] disabled:opacity-40"
            >
              Cancel analysis
            </button>
          </div>
        )}

        {/* Input */}
        <div className="shrink-0 border-t border-[--color-border] p-4">
          <div className="flex gap-2">
            <textarea
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isSending}
              placeholder={
                activeGate === 'finding_reviewer'
                  ? 'Acknowledge findings to continue…'
                  : 'Type your response…'
              }
              className={cn(
                'flex-1 resize-none rounded border border-[--color-border] bg-[--color-surface-raised]',
                'px-3 py-2 font-mono text-xs text-[--color-text] placeholder:text-[--color-muted]/40',
                'transition-colors focus:border-[--color-accent] focus:outline-none',
                'disabled:cursor-not-allowed disabled:opacity-40',
              )}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void handleSend(input)
                }
              }}
            />
            <Button
              variant="secondary"
              size="sm"
              disabled={!input.trim() || isSending}
              onClick={() => void handleSend(input)}
            >
              Send
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Run type-check**

```bash
cd apps/frontend && pnpm type-check
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add apps/frontend/src/components/chat/ apps/frontend/package.json apps/frontend/pnpm-lock.yaml
git commit -m "feat(chat): add ChatOverlay component with markdown rendering and autopilot-aware gate labels"
```

---

## Task 7: ExecutionPage

Build the primary execution view — graph as main content, chat overlay on gate activation, node detail slide-in on click.

**Files:**
- Replace: `src/pages/ExecutionPage.tsx`

**Interfaces:**
- Consumes: `useJobStatus`, `useChat` from hooks; `ChatOverlay`, `ExecutionGraph`, `NodeDetailPanel`; `mapResponseToGraphState`; `Badge`, `Spinner` from UI; `getActiveGate` from lib; types from `src/api/types.ts`
- Produces: default export `ExecutionPage`

- [ ] **Step 1: Replace `src/pages/ExecutionPage.tsx`**

```typescript
// src/pages/ExecutionPage.tsx
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useJobStatus } from '../hooks/useJobStatus'
import { useChat } from '../hooks/useChat'
import { mapResponseToGraphState } from '../components/graph/graphStateMapper'
import { ExecutionGraph } from '../components/graph/ExecutionGraph'
import { NodeDetailPanel } from '../components/graph/NodeDetailPanel'
import { ChatOverlay } from '../components/chat/ChatOverlay'
import { Badge } from '../components/ui/Badge'
import { Spinner } from '../components/ui/Spinner'
import { cn } from '../lib/utils'
import type { NodeId } from '../components/graph/graphDefinition'

function formatDuration(start: string, end: string | null): string {
  const ms = (end ? new Date(end) : new Date()).getTime() - new Date(start).getTime()
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export default function ExecutionPage() {
  const { traceId } = useParams<{ traceId: string }>()
  const navigate = useNavigate()

  const autopilot = localStorage.getItem('deprisk.autopilot') === 'true'
  const [chatOpen, setChatOpen] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<NodeId | null>(null)
  const [metaExpanded, setMetaExpanded] = useState(false)

  const { data, isPolling, error, startPolling, resume } = useJobStatus(traceId)

  const { activeGate, messages, send, isSending } = useChat(
    traceId,
    data?.artifacts ?? [],
    { autopilot, onSent: resume },
  )

  // Start polling on mount
  useEffect(() => { startPolling() }, [startPolling])

  // Auto-open chat overlay when a gate becomes active (and autopilot is off)
  useEffect(() => {
    if (activeGate && !autopilot) setChatOpen(true)
  }, [activeGate, autopilot])

  // Redirect done jobs to report page (handled by footer link — no auto-redirect)

  const renderData = mapResponseToGraphState(data)
  const status = data?.status ?? null
  const concern = data?.metadata?.concern ?? ''

  // Chat button dot state
  const chatDotClass = activeGate
    ? 'bg-[--color-accent] animate-pulse'
    : 'bg-[--color-muted]'

  return (
    <main className="flex flex-col gap-4">
      {/* Header bar */}
      <div className="flex items-center gap-3">
        <Link
          to="/jobs"
          className="shrink-0 font-mono text-xs tracking-widest text-[--color-muted] uppercase transition-colors hover:text-[--color-accent]"
        >
          ← Executions
        </Link>
        <div className="h-px flex-1 bg-[--color-border]" />
        {concern && (
          <span
            className="max-w-xs truncate font-mono text-xs text-[--color-muted]"
            title={concern}
          >
            {concern}
          </span>
        )}
        {status && <Badge status={status} />}
        {isPolling && <Spinner size="sm" />}

        {/* Chat button */}
        {!autopilot ? (
          <button
            type="button"
            onClick={() => setChatOpen(true)}
            className="flex items-center gap-1.5 rounded border border-[--color-border] bg-[--color-surface] px-3 py-1.5 font-mono text-xs text-[--color-muted] transition-colors hover:border-[--color-accent]/40 hover:text-[--color-text]"
            title={activeGate ? 'Response required' : 'View conversation'}
          >
            <span className={cn('h-1.5 w-1.5 rounded-full', chatDotClass)} />
            Chat
          </button>
        ) : (
          <span className="rounded border border-[--color-accent]/30 bg-[--color-accent]/5 px-2.5 py-1 font-mono text-[10px] tracking-widest text-[--color-accent] uppercase">
            Autopilot
          </span>
        )}
      </div>

      {/* Meta strip (collapsible) */}
      {data && (
        <button
          type="button"
          onClick={() => setMetaExpanded((v) => !v)}
          className="w-full rounded-lg border border-[--color-border] bg-[--color-surface] px-4 py-2.5 text-left transition-colors hover:bg-[--color-surface-raised]"
        >
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
              {metaExpanded ? '▾' : '▸'} Details
            </span>
            {!metaExpanded && (
              <span className="font-mono text-xs text-[--color-muted] truncate">
                {traceId}
              </span>
            )}
          </div>
          {metaExpanded && (
            <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 font-mono text-xs">
              <dt className="tracking-widest text-[--color-muted] uppercase">Trace ID</dt>
              <dd className="truncate text-[--color-text]">{traceId}</dd>
              <dt className="tracking-widest text-[--color-muted] uppercase">Repo</dt>
              <dd className="truncate text-[--color-text]">{data.metadata?.repo_url}</dd>
              {data.created_at && (
                <>
                  <dt className="tracking-widest text-[--color-muted] uppercase">Started</dt>
                  <dd className="text-[--color-text]">{new Date(data.created_at as unknown as string).toLocaleString()}</dd>
                </>
              )}
              {data.completed_at && (
                <>
                  <dt className="tracking-widest text-[--color-muted] uppercase">Duration</dt>
                  <dd className="text-[--color-text]">{formatDuration(data.created_at as unknown as string, data.completed_at)}</dd>
                </>
              )}
            </dl>
          )}
        </button>
      )}

      {/* Loading state */}
      {!data && !error && (
        <div className="flex items-center justify-center py-20">
          <Spinner size="lg" />
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="rounded-lg border border-[--color-error]/40 bg-[--color-error]/5 px-5 py-4">
          <p className="font-mono text-sm text-[--color-error]">
            <span className="font-semibold">Error: </span>{error.message}
          </p>
        </div>
      )}

      {/* Execution graph */}
      {data && (
        <ExecutionGraph
          renderData={renderData}
          selectedNodeId={selectedNodeId}
          onNodeClick={useCallback(
            (id: NodeId | null) => setSelectedNodeId(id),
            [],
          )}
          isRunning={status === 'running' || status === 'processing'}
        />
      )}

      {/* Status footer */}
      {status === 'done' && (
        <div className="flex items-center justify-end rounded-lg border border-[--color-border] bg-[--color-surface] px-5 py-4">
          <Link
            to={`/jobs/${traceId}/report`}
            className="font-mono text-sm font-semibold text-[--color-accent] transition-colors hover:text-[--color-accent-hover]"
          >
            View full report →
          </Link>
        </div>
      )}
      {status === 'failed' && (
        <div className="rounded-lg border border-[--color-error]/40 bg-[--color-error]/5 px-5 py-4">
          <p className="font-mono text-sm text-[--color-error]">
            Analysis failed. Check discovery steps for details.
          </p>
        </div>
      )}
      {status === 'cancelled' && (
        <div className="rounded-lg border border-[--color-border] bg-[--color-surface] px-5 py-4">
          <p className="font-mono text-sm text-[--color-muted]">Analysis was cancelled.</p>
        </div>
      )}

      {/* Node detail panel (slide-in) */}
      {selectedNodeId && data && (
        <NodeDetailPanel
          nodeId={selectedNodeId}
          results={data.results}
          artifacts={data.artifacts}
          onClose={() => setSelectedNodeId(null)}
        />
      )}

      {/* Chat overlay */}
      <ChatOverlay
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        activeGate={activeGate}
        messages={messages}
        isSending={isSending}
        onSend={send}
      />
    </main>
  )
}
```

- [ ] **Step 2: Fix `data.created_at` type** — `StatusResponse` doesn't include `created_at`. Remove those lines from the meta strip or use `data.completed_at` only. Replace the `formatDuration` call and the `Started` row with:

```typescript
{data.completed_at && (
  <>
    <dt className="tracking-widest text-[--color-muted] uppercase">Completed</dt>
    <dd className="text-[--color-text]">{new Date(data.completed_at).toLocaleString()}</dd>
  </>
)}
```

And remove `formatDuration` function and its call since `StatusResponse` has no `created_at`.

- [ ] **Step 3: Run type-check**

```bash
cd apps/frontend && pnpm type-check
```

Expected: 0 errors.

- [ ] **Step 4: Start dev server and verify manually**

```bash
cd apps/frontend && pnpm dev
```

Navigate to an existing job URL `/jobs/<some-trace-id>`. Verify:
- Graph renders with correct nodes
- Status badge updates
- Clicking a node opens the slide-in panel
- Chat button opens overlay
- "View full report →" appears when `status === done`

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/src/pages/ExecutionPage.tsx
git commit -m "feat(page): add ExecutionPage — graph + chat overlay + node detail slide-in"
```

---

## Task 8: Report Page

Build the structured report view reading from `results.analysis_report`.

**Files:**
- Replace: `src/pages/ReportPage.tsx`

**Interfaces:**
- Consumes: `useReport` hook; `AnalysisReport`, `ReportFinding`, `Severity` from `src/api/types.ts`
- Produces: default export `ReportPage`

- [ ] **Step 1: Replace `src/pages/ReportPage.tsx`**

```typescript
// src/pages/ReportPage.tsx
import { useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { useReport } from '../hooks/useReport'
import { Spinner } from '../components/ui/Spinner'
import { cn } from '../lib/utils'
import type { ReportFinding, Severity } from '../api/types'

const SEVERITY_STYLES: Record<Severity, string> = {
  critical: 'text-red-400 border-red-500/50 bg-red-500/10',
  high:     'text-orange-400 border-orange-500/50 bg-orange-500/10',
  medium:   'text-yellow-400 border-yellow-500/50 bg-yellow-500/10',
  low:      'text-blue-400 border-blue-500/50 bg-blue-500/10',
  info:     'text-[--color-muted] border-[--color-border] bg-[--color-surface-raised]',
}

const OVERALL_SEVERITY_STYLES: Record<string, string> = {
  critical: 'text-red-400',
  high:     'text-orange-400',
  medium:   'text-yellow-400',
  low:      'text-blue-400',
  none:     'text-[--color-muted]',
}

function RiskBar({ score }: { score: number }) {
  const pct = Math.min(100, (score / 10) * 100)
  const colorClass =
    score >= 8 ? 'bg-red-400' :
    score >= 6 ? 'bg-orange-400' :
    score >= 4 ? 'bg-yellow-400' :
                 'bg-blue-400'
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-[--color-border]">
        <div className={cn('h-full rounded-full', colorClass)} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-[10px] text-[--color-muted]">{score.toFixed(1)}/10</span>
    </div>
  )
}

function FindingRow({ finding }: { finding: ReportFinding }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border border-[--color-border] bg-[--color-surface]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-4 px-5 py-4 text-left"
      >
        <span
          className={cn(
            'shrink-0 rounded border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase',
            SEVERITY_STYLES[finding.severity],
          )}
        >
          {finding.severity}
        </span>
        <span className="min-w-0 flex-1 font-mono text-sm font-semibold text-[--color-text]">
          {finding.dep_name}
        </span>
        <RiskBar score={finding.risk_score} />
        <span className="font-mono text-[10px] text-[--color-muted]">
          {Math.round(finding.confidence * 100)}% confidence
        </span>
        <span className="font-mono text-xs text-[--color-muted]">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="space-y-4 border-t border-[--color-border] px-5 py-4">
          <p className="font-mono text-xs leading-relaxed text-[--color-muted]">{finding.summary}</p>

          {finding.recommendation && (
            <div>
              <p className="mb-1 font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
                Recommendation
              </p>
              <p className="font-mono text-xs text-[--color-text]">{finding.recommendation}</p>
            </div>
          )}

          {finding.alternatives.length > 0 && (
            <div>
              <p className="mb-1 font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
                Alternatives
              </p>
              <div className="flex flex-wrap gap-1.5">
                {finding.alternatives.map((alt) => (
                  <span
                    key={alt}
                    className="rounded border border-[--color-border] bg-[--color-surface-raised] px-2 py-0.5 font-mono text-[10px] text-[--color-text]"
                  >
                    {alt}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="flex gap-6 font-mono text-[10px] text-[--color-muted]">
            <span>{finding.supporting_evidence_count} evidence items</span>
            {finding.contradictions_count > 0 && (
              <span>{finding.contradictions_count} contradictions</span>
            )}
          </div>

          {finding.missing_evidence.length > 0 && (
            <div>
              <p className="mb-1 font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
                Missing evidence
              </p>
              <ul className="space-y-0.5">
                {finding.missing_evidence.map((m) => (
                  <li key={m} className="font-mono text-[10px] text-[--color-muted]">• {m}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function ReportPage() {
  const { traceId } = useParams<{ traceId: string }>()
  const { report, isLoading, error } = useReport(traceId)

  if (isLoading) {
    return (
      <main className="flex items-center justify-center py-20">
        <Spinner size="lg" />
      </main>
    )
  }

  if (error) {
    return (
      <main className="space-y-4">
        <Link to={`/jobs/${traceId}`} className="font-mono text-xs tracking-widest text-[--color-muted] uppercase hover:text-[--color-accent]">
          ← Execution view
        </Link>
        <div className="rounded-lg border border-[--color-error]/40 bg-[--color-error]/5 px-5 py-4">
          <p className="font-mono text-sm text-[--color-error]">{error.message}</p>
        </div>
      </main>
    )
  }

  // No report yet — redirect back to execution view
  if (!report) return <Navigate to={`/jobs/${traceId}`} replace />

  const overallColor = OVERALL_SEVERITY_STYLES[report.overall_risk_level] ?? OVERALL_SEVERITY_STYLES.none

  return (
    <main className="space-y-8 pb-16">
      {/* Breadcrumb */}
      <Link
        to={`/jobs/${traceId}`}
        className="font-mono text-xs tracking-widest text-[--color-muted] uppercase transition-colors hover:text-[--color-accent]"
      >
        ← Execution view
      </Link>

      {/* Report header */}
      <div className="space-y-2 rounded-xl border border-[--color-border] bg-[--color-surface] p-6">
        <div className="flex flex-wrap items-center gap-3">
          <span className={cn('font-mono text-xs font-bold uppercase tracking-widest', overallColor)}>
            Overall: {report.overall_risk_level}
          </span>
          <span className="font-mono text-xs text-[--color-muted]">
            {report.findings.length} findings · {new Date(report.generated_at).toLocaleString()}
          </span>
        </div>
        <p className="font-mono text-xs text-[--color-muted]">{report.concern}</p>
      </div>

      {/* Summary strip */}
      <div className="grid grid-cols-4 gap-3">
        {(['critical', 'high', 'medium', 'low'] as const).map((sev) => (
          <div key={sev} className="rounded-lg border border-[--color-border] bg-[--color-surface] p-4 text-center">
            <p className={cn('font-mono text-2xl font-bold', SEVERITY_STYLES[sev].split(' ')[0])}>
              {report.summary[sev]}
            </p>
            <p className="font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">{sev}</p>
          </div>
        ))}
      </div>

      {/* Findings */}
      {report.findings.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center gap-3">
            <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
              Findings
            </span>
            <div className="h-px flex-1 bg-[--color-border]" />
          </div>
          <div className="space-y-2">
            {report.findings.map((f, i) => (
              <FindingRow key={f.dep_name} finding={f} />
            ))}
          </div>
        </section>
      )}

      {/* Recommendations */}
      {report.recommendations.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center gap-3">
            <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
              Recommendations
            </span>
            <div className="h-px flex-1 bg-[--color-border]" />
          </div>
          <ol className="space-y-2">
            {report.recommendations.map((r, i) => (
              <li key={i} className="flex gap-3 font-mono text-xs text-[--color-text]">
                <span className="shrink-0 text-[--color-accent]">{i + 1}.</span>
                {r}
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* Contradictions */}
      {report.contradictions.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center gap-3">
            <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
              Contradictions
            </span>
            <div className="h-px flex-1 bg-[--color-border]" />
          </div>
          <div className="space-y-2">
            {report.contradictions.map((c, i) => (
              <div key={i} className="flex items-start gap-4 rounded-lg border border-[--color-border] bg-[--color-surface] px-5 py-3">
                <p className="flex-1 font-mono text-xs text-[--color-muted]">{c.description}</p>
                <span className="shrink-0 rounded border border-[--color-border] bg-[--color-surface-raised] px-2 py-0.5 font-mono text-[10px] text-[--color-muted]">
                  {c.resolution}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  )
}
```

- [ ] **Step 2: Run type-check**

```bash
cd apps/frontend && pnpm type-check
```

Expected: 0 errors.

- [ ] **Step 3: Test manually**

Navigate to `/jobs/<done-trace-id>/report`. Verify:
- Report header shows overall risk and concern
- Summary strip shows critical/high/medium/low counts
- Each finding row expands on click
- "← Execution view" returns to `/jobs/:traceId`
- If `report` is null (job not done), page redirects to execution view

- [ ] **Step 4: Commit**

```bash
git add apps/frontend/src/pages/ReportPage.tsx
git commit -m "feat(page): add ReportPage with accordion findings, severity indicators, recommendations"
```

---

## Task 9: New Analysis Page

Build the `/new` form with repo URL input, concern textarea, pre-built concern pills, autopilot toggle, and sample cards.

**Files:**
- Replace: `src/pages/NewAnalysisPage.tsx`

**Interfaces:**
- Consumes: `useJobSubmit`; `SAMPLES` from `src/data/samples.ts`; `Button`, `Input`, `Textarea`, `Spinner` UI primitives
- Produces: default export `NewAnalysisPage`

- [ ] **Step 1: Replace `src/pages/NewAnalysisPage.tsx`**

```typescript
// src/pages/NewAnalysisPage.tsx
import { useState } from 'react'
import { useJobSubmit } from '../hooks/useJobSubmit'
import { SAMPLES } from '../data/samples'
import { Button } from '../components/ui/Button'
import { Spinner } from '../components/ui/Spinner'
import { cn } from '../lib/utils'

const CONCERN_PILLS = [
  'Supply chain risks',
  'Known CVEs',
  'License compliance',
  'Maintainer trust',
  'Blast radius',
]

const AUTOPILOT_KEY = 'deprisk.autopilot'

function loadAutopilot(): boolean {
  return localStorage.getItem(AUTOPILOT_KEY) === 'true'
}

function saveAutopilot(value: boolean): void {
  localStorage.setItem(AUTOPILOT_KEY, String(value))
}

interface FormErrors {
  repo_url?: string
  concern?: string
}

export default function NewAnalysisPage() {
  const [repoUrl, setRepoUrl] = useState('')
  const [concern, setConcern] = useState('')
  const [autopilot, setAutopilot] = useState(loadAutopilot)
  const [errors, setErrors] = useState<FormErrors>({})
  const { submit, isSubmitting, error: submitError } = useJobSubmit()

  function validate(): boolean {
    const next: FormErrors = {}
    if (!repoUrl.trim()) next.repo_url = 'Repository URL is required'
    else if (!repoUrl.startsWith('https://')) next.repo_url = 'URL must start with https://'
    if (!concern.trim()) next.concern = 'Describe the risk you want to investigate'
    setErrors(next)
    return Object.keys(next).length === 0
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!validate()) return
    await submit({ repo_url: repoUrl.trim(), concern: concern.trim() })
  }

  function handleAutopilotChange(checked: boolean) {
    setAutopilot(checked)
    saveAutopilot(checked)
  }

  return (
    <main className="space-y-8">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
          new analysis
        </span>
        <div className="h-px flex-1 bg-[--color-border]" />
      </div>

      <div className="grid gap-10 lg:grid-cols-2">
        {/* ── Left: form ── */}
        <form onSubmit={(e) => void handleSubmit(e)} noValidate className="space-y-6">
          {/* Repo URL */}
          <div className="space-y-1.5">
            <label htmlFor="repo-url" className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
              GitHub Repository URL
            </label>
            <input
              id="repo-url"
              type="url"
              value={repoUrl}
              onChange={(e) => { setRepoUrl(e.target.value); setErrors((p) => ({ ...p, repo_url: undefined })) }}
              placeholder="https://github.com/org/repo"
              disabled={isSubmitting}
              className={cn(
                'w-full rounded border bg-[--color-surface-raised] px-3 py-2 font-mono text-sm text-[--color-text]',
                'placeholder:text-[--color-muted]/40 transition-colors focus:outline-none',
                errors.repo_url
                  ? 'border-[--color-error] focus:border-[--color-error]'
                  : 'border-[--color-border] focus:border-[--color-accent]',
                'disabled:cursor-not-allowed disabled:opacity-40',
              )}
            />
            {errors.repo_url && (
              <p className="font-mono text-[10px] text-[--color-error]">{errors.repo_url}</p>
            )}
          </div>

          {/* Concern */}
          <div className="space-y-1.5">
            <label htmlFor="concern" className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
              What risk do you want to investigate?
            </label>
            <textarea
              id="concern"
              rows={3}
              value={concern}
              onChange={(e) => { setConcern(e.target.value); setErrors((p) => ({ ...p, concern: undefined })) }}
              placeholder='e.g. "supply chain attack via malicious postinstall scripts"'
              disabled={isSubmitting}
              className={cn(
                'w-full resize-none rounded border bg-[--color-surface-raised] px-3 py-2 font-mono text-sm text-[--color-text]',
                'placeholder:text-[--color-muted]/40 transition-colors focus:outline-none',
                errors.concern
                  ? 'border-[--color-error] focus:border-[--color-error]'
                  : 'border-[--color-border] focus:border-[--color-accent]',
                'disabled:cursor-not-allowed disabled:opacity-40',
              )}
            />
            {errors.concern && (
              <p className="font-mono text-[10px] text-[--color-error]">{errors.concern}</p>
            )}
          </div>

          {/* Concern pills */}
          <div className="space-y-2">
            <p className="font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
              or pick a concern
            </p>
            <div className="flex flex-wrap gap-2">
              {CONCERN_PILLS.map((pill) => (
                <button
                  key={pill}
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => { setConcern(pill); setErrors((p) => ({ ...p, concern: undefined })) }}
                  className={cn(
                    'rounded-full border px-3 py-1 font-mono text-[10px] transition-colors',
                    concern === pill
                      ? 'border-[--color-accent] bg-[--color-accent]/10 text-[--color-accent]'
                      : 'border-[--color-border] bg-[--color-surface-raised] text-[--color-muted] hover:border-[--color-accent]/40 hover:text-[--color-text]',
                    'disabled:cursor-not-allowed disabled:opacity-40',
                  )}
                >
                  {pill}
                </button>
              ))}
            </div>
          </div>

          {/* Autopilot */}
          <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-[--color-border] bg-[--color-surface] p-4">
            <input
              type="checkbox"
              checked={autopilot}
              onChange={(e) => handleAutopilotChange(e.target.checked)}
              className="mt-0.5 accent-[--color-accent]"
            />
            <div>
              <p className="font-mono text-xs font-semibold text-[--color-text]">Autopilot mode</p>
              <p className="font-mono text-[10px] leading-relaxed text-[--color-muted]">
                The AI auto-approves both review gates and runs to completion without asking for your input.
              </p>
            </div>
          </label>

          {/* API error */}
          {submitError && (
            <div className="rounded-lg border border-[--color-error]/40 bg-[--color-error]/5 px-4 py-3">
              <p className="font-mono text-xs text-[--color-error]">{submitError.message}</p>
            </div>
          )}

          <div className="flex justify-end">
            <Button type="submit" disabled={isSubmitting} size="md">
              {isSubmitting ? (
                <>
                  <Spinner size="sm" />
                  Starting…
                </>
              ) : (
                'Run analysis →'
              )}
            </Button>
          </div>
        </form>

        {/* ── Right: sample repos ── */}
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
              sample repositories
            </span>
            <div className="h-px flex-1 bg-[--color-border]" />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {SAMPLES.map((sample) => (
              <button
                key={sample.id}
                type="button"
                disabled={isSubmitting}
                onClick={() => {
                  setRepoUrl(sample.repo_url)
                  setConcern(sample.concern)
                  setErrors({})
                }}
                className="group rounded-xl border border-[--color-border] bg-[--color-surface] p-5 text-left transition-all duration-200 hover:border-[--color-accent]/40 hover:bg-[--color-surface-raised] disabled:cursor-not-allowed disabled:opacity-40"
              >
                <div className="mb-2 flex items-start justify-between gap-2">
                  <span className="font-display text-sm font-bold text-[--color-text]">
                    {sample.label}
                  </span>
                  <span className="font-mono text-xs text-[--color-muted] transition-colors group-hover:text-[--color-accent]">
                    use →
                  </span>
                </div>
                <p className="font-mono text-[10px] leading-relaxed text-[--color-muted]">
                  {sample.description}
                </p>
              </button>
            ))}
          </div>
        </div>
      </div>
    </main>
  )
}
```

- [ ] **Step 2: Run type-check**

```bash
cd apps/frontend && pnpm type-check
```

Expected: 0 errors.

- [ ] **Step 3: Test manually**

```bash
pnpm dev
```

Navigate to `/new`. Verify:
- Repo URL and concern fields validate correctly
- Concern pills fill the textarea
- Autopilot toggle persists after page reload (`localStorage`)
- Submitting navigates to `/jobs/:traceId`
- Sample cards fill both fields

- [ ] **Step 4: Commit**

```bash
git add apps/frontend/src/pages/NewAnalysisPage.tsx
git commit -m "feat(page): add NewAnalysisPage — repo URL form, concern pills, autopilot toggle, sample cards"
```

---

## Task 10: JobsListPage + LandingPage + Header + Final Cleanup

Update the remaining pages, fix navigation, and verify the whole app compiles and lints cleanly.

**Files:**
- Modify: `src/pages/JobsListPage.tsx`
- Modify: `src/pages/LandingPage.tsx`
- Modify: `src/components/layout/Header.tsx`

**Interfaces:**
- Consumes: all types from `src/api/types.ts`; `getJobs` from `src/api/client.ts`

- [ ] **Step 1: Update `src/pages/JobsListPage.tsx`**

Three targeted changes:
1. Add "New analysis →" button in the page header (right-aligned, links to `/new`)
2. Add `awaiting_approval` and `processing` to `STATUS_OPTIONS`
3. Row click: `done` rows navigate to `/jobs/:traceId/report`, all others to `/jobs/:traceId`

```typescript
// Change 1 — page header section (add Link import from react-router-dom):
import { Link, useNavigate } from 'react-router-dom'

// In the header div, add:
<div className="flex items-center gap-3">
  <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
    executions
  </span>
  <div className="h-px flex-1 bg-[--color-border]" />
  {data && <span className="font-mono text-xs text-[--color-muted]">{data.total} total</span>}
  <Link
    to="/new"
    className="rounded border border-[--color-accent]/40 bg-[--color-accent]/5 px-3 py-1.5 font-mono text-xs text-[--color-accent] transition-colors hover:bg-[--color-accent]/10"
  >
    New analysis →
  </Link>
</div>

// Change 2 — STATUS_OPTIONS:
const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'pending', label: 'pending' },
  { value: 'running', label: 'running' },
  { value: 'processing', label: 'processing' },
  { value: 'awaiting_approval', label: 'awaiting approval' },
  { value: 'done', label: 'done' },
  { value: 'failed', label: 'failed' },
  { value: 'cancelled', label: 'cancelled' },
]

// Change 3 — row onClick:
onClick={() =>
  navigate(item.status === 'done' ? `/jobs/${item.trace_id}/report` : `/jobs/${item.trace_id}`)
}
```

- [ ] **Step 2: Update `src/pages/LandingPage.tsx`**

Update the STEPS array and CTA route:

```typescript
// Replace STEPS:
const STEPS = [
  { n: '01', label: 'Enter repository URL', detail: 'Paste any GitHub repository URL' },
  { n: '02', label: 'AI builds the analysis plan', detail: 'Hypotheses and skill assignments generated' },
  { n: '03', label: 'Review and approve', detail: 'Inspect the plan before execution starts' },
  { n: '04', label: 'Receive actionable report', detail: 'Prioritised risks with remediation guidance' },
]

// Replace both navigate('/scan') calls with navigate('/new')
```

- [ ] **Step 3: Update `src/components/layout/Header.tsx`**

Find the "scan" or "Scan" nav link and update it to point to `/new` with the label "New analysis". Keep all other header markup unchanged.

- [ ] **Step 4: Run full type-check and lint**

```bash
cd apps/frontend && pnpm type-check && pnpm lint
```

Expected: 0 type errors, 0 lint errors.

- [ ] **Step 5: Full manual smoke test**

```bash
pnpm dev
```

Walk through the complete user flow:
1. `/` — landing page renders, "Start scanning →" goes to `/new`
2. `/new` — form validates, submitting navigates to execution page
3. `/jobs/:traceId` — graph renders, chat button visible, polling active
4. When `awaiting_approval` — chat overlay opens automatically, quick actions visible
5. After approval — overlay closes, polling resumes, graph updates
6. When `done` — "View full report →" footer appears
7. `/jobs/:traceId/report` — report renders, "← Execution view" link works
8. `/jobs` — list shows all jobs, "New analysis →" button present, done rows go to report page
9. Node click — slide-in panel appears with correct content per node type

- [ ] **Step 6: Final commit**

```bash
git add apps/frontend/src/
git commit -m "feat(frontend): complete redesign — update JobsListPage, LandingPage, Header; wire all routes"
```
