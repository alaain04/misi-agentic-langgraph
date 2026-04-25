export type LockFileName = 'package-lock.json' | 'yarn.lock' | 'pnpm-lock.yaml'

export type JobStatus = 'pending' | 'running' | 'done' | 'failed'

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

export interface DiscoveryResult {
  project_metadata?: ProjectMetadata
  direct_dependencies?: DependencyEntry[]
  transitive_dependencies?: DependencyEntry[]
  manifest_files?: string[]
  discovery_summary?: string
  discovery_error?: string | null
}

export interface StatusResponse {
  trace_id: string
  status: JobStatus
  concern: string
  completed_at: string | null
  result?: DiscoveryResult
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
