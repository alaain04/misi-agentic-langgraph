import { useCallback, useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getJobStatus } from '../api/client'
import type { StatusResponse } from '../api/types'
import { Badge } from '../components/ui/Badge'
import { Spinner } from '../components/ui/Spinner'
import { AnalysisResult } from '../components/analysis/AnalysisResult'
import { usePolling } from '../hooks/usePolling'

function formatDate(iso: string | null): string {
  if (!iso) return '\u2014'
  return new Date(iso).toLocaleString()
}

export default function JobDetailPage() {
  const { traceId } = useParams<{ traceId: string }>()

  const fetchStatus = useCallback((): Promise<StatusResponse> => {
    if (!traceId) return Promise.reject(new Error('No trace ID'))
    return getJobStatus(traceId)
  }, [traceId])

  const shouldStop = useCallback((data: StatusResponse): boolean => {
    return data.status === 'done' || data.status === 'failed'
  }, [])

  const { data, error, isPolling, startPolling } = usePolling<StatusResponse>(
    fetchStatus,
    2000,
    shouldStop,
  )

  // Start polling immediately on mount (or when traceId changes).
  useEffect(() => {
    startPolling()
  }, [startPolling])

  const isTerminal = data?.status === 'done' || data?.status === 'failed'

  return (
    <main className="space-y-6">
      {/* Back link */}
      <div>
        <Link
          to="/jobs"
          className="font-mono text-xs tracking-widest text-[--color-muted] uppercase transition-colors hover:text-[--color-accent]"
        >
          ← back to executions
        </Link>
      </div>

      {/* Meta card — shown as soon as we have any data */}
      {data && (
        <div className="space-y-3 rounded-lg border border-l-2 border-[--color-border] border-l-[--color-accent] bg-white/[0.03] px-5 py-4">
          <div className="flex items-center gap-3">
            <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
              01 / execution
            </span>
            <div className="h-px flex-1 bg-[--color-border]" />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Badge status={data.status} />
            {isPolling && (
              <span className="flex items-center gap-1.5 font-mono text-xs text-[--color-muted]">
                <Spinner size="sm" />
                polling…
              </span>
            )}
          </div>

          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 font-mono text-xs">
            <dt className="tracking-widest text-[--color-muted] uppercase">Trace ID</dt>
            <dd className="truncate text-[--color-text]" title={data.trace_id}>
              {data.trace_id}
            </dd>

            <dt className="tracking-widest text-[--color-muted] uppercase">Concern</dt>
            <dd className="text-[--color-text]">{data.concern}</dd>

            <dt className="tracking-widest text-[--color-muted] uppercase">Processed</dt>
            <dd className="text-[--color-text]">{formatDate(data.completed_at)}</dd>
          </dl>
        </div>
      )}

      {/* Loading state — no data yet */}
      {!data && !error && (
        <div className="flex items-center justify-center py-16">
          <Spinner size="lg" />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-[--color-error]/40 bg-[--color-error]/5 px-5 py-4">
          <p className="font-mono text-sm text-[--color-error]">
            <span className="font-semibold">Error: </span>
            {error.message}
          </p>
        </div>
      )}

      {/* Result — only when terminal */}
      {data && isTerminal && <AnalysisResult status={data.status} result={data.result} />}
    </main>
  )
}
