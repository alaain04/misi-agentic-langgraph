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
  error: string | null
  artifacts: Artifact[]
  graph: GraphInfo
}

export interface SubmitResponse {
  trace_id: string
  status: JobStatus
}
