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

export interface EvidenceRef {
  tool: string
  url: string | null
  log_snippet: string
}

export interface AnalysisRequest {
  repo_url: string
  concern: string
  autopilot?: boolean
  remediate?: boolean
}

export interface JobMetadata {
  repo_url: string
  concern: string
  autopilot: boolean
  remediate: boolean
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
  lock_generation_error: string | null
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
  severity: Severity
  description: string
  recommendation: string | null
  evidence?: EvidenceRef[]
}

export interface AnalysisReport {
  concern: string
  generated_at: string
  overall_risk_level: Severity | 'none'
  executive_summary: string
  findings: ReportFinding[]
  recommendations: string[]
}

export interface JobResult {
  analysis_report: AnalysisReport | null
  discovery?: DiscoveryResult | null
}

// ── Artifacts ──────────────────────────────────────────────────────────────────

export type ArtifactStatus = 'running' | 'done' | 'failed' | 'cancelled'

export interface ArtifactMessage {
  role: 'assistant' | 'human'
  content: string
  created_at: string
  type?: 'ask_user' | 'checkpoint'
  action?: 'approve' | 'change' | 'cancel'
}

interface BaseArtifact {
  node: string
  status: ArtifactStatus
  started_at: string
  completed_at: string | null
}

// ── New ReAct graph artifacts ──

export interface PrepArtifact extends BaseArtifact {
  node: 'prep'
}

export interface ToolCall {
  tool: string
  args: Record<string, unknown>
}

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

export interface ToolError {
  tool: string
  error: string
}

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

export interface HitlGateArtifact extends BaseArtifact {
  node: 'hitl_gate'
  messages: ArtifactMessage[]
}

// ── Legacy graph artifacts (kept for compatibility) ──

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
  data?: { risk_findings: ReportFinding[] }
  output?: { review_approved: boolean; reviewer_feedback: string | null }
  messages: ArtifactMessage[]
}

export interface ReportArtifact extends BaseArtifact {
  node: 'report_builder'
  output?: AnalysisReport
}

export type Artifact =
  | PrepArtifact
  | ConductorArtifact
  | ToolRunnerArtifact
  | HitlGateArtifact
  | DiscoveryArtifact
  | PlannerArtifact
  | CollectorArtifact
  | CorrelatorArtifact
  | ReviewerArtifact
  | ReportArtifact

// ── API responses ─────────────────────────────────────────────────────────────

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

export interface SubmitResponse {
  trace_id: string
  status: JobStatus
}
