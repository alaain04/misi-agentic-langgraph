export type LockFileName = 'package-lock.json' | 'yarn.lock' | 'pnpm-lock.yaml'

export type JobStatus = 'pending' | 'running' | 'awaiting_approval' | 'done' | 'failed'

export interface AnalysisRequest {
  package_json: string
  lock_file: string
  lock_file_name: LockFileName
  concern: string
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
  final_report?: string
}

export interface ArtifactInfo {
  node: string
  status: 'running' | 'done' | 'failed'
  started_at: string
  completed_at: string | null
}

export interface StatusResponse {
  trace_id: string
  status: JobStatus
  concern: string
  package_json: string | null
  lock_file_name: string | null
  completed_at: string | null
  results?: AnalysisResult
  artifacts?: ArtifactInfo[]
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

export interface PlanApprovalRequest {
  action: 'approve' | 'modify' | 'cancel' | 'refine'
  plan?: string[]
  feedback?: string
}
