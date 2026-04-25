import { cn } from '../../lib/utils'
import type { JobStatus } from '../../api/types'

interface BadgeProps {
  status: JobStatus
  className?: string
}

const variants: Record<JobStatus, string> = {
  pending: 'bg-[--badge-pending-bg] text-[--badge-pending-text] border-[--badge-pending-border]',
  running: 'bg-[--badge-running-bg] text-[--badge-running-text] border-[--badge-running-border]',
  done: 'bg-[--badge-done-bg] text-[--badge-done-text] border-[--badge-done-border]',
  failed: 'bg-[--badge-failed-bg] text-[--badge-failed-text] border-[--badge-failed-border]',
}

const labels: Record<JobStatus, string> = {
  pending: 'pending',
  running: 'running',
  done: 'done',
  failed: 'failed',
}

export function Badge({ status, className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded border px-2.5 py-0.5 font-mono text-xs font-medium tracking-widest uppercase',
        variants[status],
        className,
      )}
    >
      <span
        className={cn(
          'size-1.5 rounded-full',
          status === 'running' && 'animate-pulse bg-current',
          status !== 'running' && 'bg-current',
        )}
      />
      {labels[status]}
    </span>
  )
}
