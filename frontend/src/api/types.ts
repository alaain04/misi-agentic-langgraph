export type LockFileName = 'package-lock.json' | 'yarn.lock' | 'pnpm-lock.yaml'

export type JobStatus =
  | 'pending'
  | 'running'
  | 'processing'
  | 'awaiting_approval'
  | 'done'
  | 'failed'
  | 'cancelled'

export interface JobMetadata {
  package_json: string
  lock_file: string
  lock_file_name: LockFileName
  concern: string
}

export interface AnalysisRequest {
  metadata: JobMetadata
}

export interface AnalysisResponse {
  trace_id: string
  status: JobStatus
}

export interface ProjectMetadata {
  name: string
  package_manager: string
  direct_dependencies_count: number
}

export interface DependencyEntry {
  name: string
  version_spec: string
}

export interface DependencyTreeNode {
  version: string
  deps: Record<string, DependencyTreeNode>
  circular?: boolean
}

export type DependencyTree = Record<string, DependencyTreeNode>

export interface DepTreeDatum {
  name: string
  version: string
  circular?: boolean
  children?: DepTreeDatum[]
}

export interface DiscoveryResult {
  project_metadata?: ProjectMetadata
  direct_dependencies?: DependencyEntry[]
  transitive_dependencies?: DependencyEntry[]
  manifest_files?: string[]
  discovery_summary?: string
  discovery_error?: string | null
  dependency_tree?: DependencyTree
}

export interface SubgraphResult {
  subgraph: string
  data: unknown
}

export interface AnalysisResult {
  discovery?: DiscoveryResult
  plan?: string[]
  subgraph_results?: SubgraphResult[]
  summary?: string
  review?: string
  recommendation?: string
}

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

export interface Proposal {
  created_at: string
  plan: string[]
  assistant_message: string
  user_response?: string
  user_intended_action?: 'approve' | 'change' | 'cancel'
}

export interface ArtifactInfo {
  node: string
  status: 'running' | 'done' | 'failed' | 'cancelled'
  started_at: string
  completed_at: string | null
  // Orchestrator-specific
  proposals?: Proposal[]
  // Subgraph-specific
  result?: Record<string, unknown>
  // Terminal node-specific
  output?: string
}

export interface StatusResponse {
  trace_id: string
  status: JobStatus
  metadata: JobMetadata
  completed_at: string | null
  results?: AnalysisResult
  artifacts?: ArtifactInfo[]
  graph?: GraphInfo
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

export interface ErrorResponse {
  detail: string
}
