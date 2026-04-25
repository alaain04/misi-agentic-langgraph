import { Badge } from '../ui/Badge'
import { Spinner } from '../ui/Spinner'
import type { DiscoveryResult, JobStatus } from '../../api/types'

interface AnalysisStatusProps {
  traceId: string
  status: JobStatus
  discovery?: DiscoveryResult
}

const statusMessages: Record<JobStatus, string> = {
  pending: 'Job queued — waiting for a worker slot',
  running: 'Pipeline executing — LangGraph is processing your dependencies',
  awaiting_approval: 'Plan ready — review the proposed analysis steps below before executing',
  done: 'Analysis complete',
  failed: 'The pipeline encountered an unrecoverable error',
}

export function AnalysisStatus({ traceId, status, discovery }: AnalysisStatusProps) {
  const isActive = status === 'pending' || status === 'running'
  const isAwaiting = status === 'awaiting_approval'
  const hasMetadata = !!discovery?.project_metadata

  return (
    <div className="space-y-4 rounded-lg border border-[--color-border] bg-[--color-surface] p-6">
      {/* Section label */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
          02 / status
        </span>
        <div className="h-px flex-1 bg-[--color-border]" />
      </div>

      <div className={hasMetadata ? 'grid grid-cols-2 gap-6' : undefined}>
        {/* Left column: status info */}
        <div className="space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1">
              <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
                trace id
              </p>
              <p className="font-mono text-sm break-all text-[--color-text]">{traceId}</p>
            </div>
            <Badge status={status} />
          </div>

          <div className="flex items-center gap-3">
            {isActive && <Spinner size="sm" />}
            <p className="font-mono text-sm text-[--color-muted]">{statusMessages[status]}</p>
          </div>

          {isActive && (
            <div className="relative h-px w-full overflow-hidden bg-[--color-border]">
              <div className="absolute inset-y-0 left-0 w-1/3 animate-[shimmer_1.8s_ease-in-out_infinite] bg-[--color-accent]/60" />
            </div>
          )}

          {isAwaiting && (
            <div className="h-px w-full bg-[--badge-awaiting-border]" />
          )}
        </div>

        {/* Right column: project metadata + manifest files */}
        {hasMetadata && (
          <div className="flex gap-8 border-l border-[--color-border] pl-6">
            <div className="space-y-2">
              <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
                Project
              </p>
              <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 font-mono text-xs">
                <dt className="tracking-widest text-[--color-muted] uppercase">Name</dt>
                <dd className="text-[--color-text]">{discovery!.project_metadata!.name}</dd>

                <dt className="tracking-widest text-[--color-muted] uppercase">Manager</dt>
                <dd className="text-[--color-text]">{discovery!.project_metadata!.package_manager}</dd>
              </dl>
            </div>
{/* 
          </div>
        )}
      </div>
    </div>
  )
}
