import { Badge } from '../ui/Badge'
import { Spinner } from '../ui/Spinner'
import type { JobStatus } from '../../api/types'

interface AnalysisStatusProps {
  traceId: string
  status: JobStatus
}

const statusMessages: Record<JobStatus, string> = {
  pending: 'Job queued — waiting for a worker slot',
  running: 'Pipeline executing — LangGraph is processing your dependencies',
  done: 'Analysis complete',
  failed: 'The pipeline encountered an unrecoverable error',
}

export function AnalysisStatus({ traceId, status }: AnalysisStatusProps) {
  const isActive = status === 'pending' || status === 'running'

  return (
    <div className="space-y-4 rounded-lg border border-[--color-border] bg-[--color-surface] p-6">
      {/* Section label */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
          02 / status
        </span>
        <div className="h-px flex-1 bg-[--color-border]" />
      </div>

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
    </div>
  )
}
