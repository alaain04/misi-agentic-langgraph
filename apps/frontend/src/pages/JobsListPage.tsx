import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getJobs } from '../api/client'
import type { JobStatus, JobsListResponse } from '../api/types'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Input } from '../components/ui/Input'
import { Select } from '../components/ui/Select'
import { Spinner } from '../components/ui/Spinner'

const PAGE_SIZE = 10

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

function formatDate(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

function truncateTraceId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) + '\u2026' : id
}

export default function JobsListPage() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [traceIdFilter, setTraceIdFilter] = useState('')
  const [data, setData] = useState<JobsListResponse | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  // Debounce trace ID input
  const traceIdDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [debouncedTraceId, setDebouncedTraceId] = useState('')

  const fetchPage = useCallback((p: number, status: string, traceId: string) => {
    setIsLoading(true)
    setError(null)
    getJobs(p, PAGE_SIZE, status || undefined, traceId || undefined)
      .then((res) => {
        setData(res)
        setPage(p)
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err : new Error(String(err)))
      })
      .finally(() => {
        setIsLoading(false)
      })
  }, [])

  // Fetch when page or filters change
  useEffect(() => {
    fetchPage(page, statusFilter, debouncedTraceId)
  }, [fetchPage, page, statusFilter, debouncedTraceId])

  function handleStatusChange(e: React.ChangeEvent<HTMLSelectElement>) {
    setStatusFilter(e.target.value)
    setPage(1)
  }

  function handleTraceIdChange(e: React.ChangeEvent<HTMLInputElement>) {
    const value = e.target.value
    setTraceIdFilter(value)
    if (traceIdDebounceRef.current) clearTimeout(traceIdDebounceRef.current)
    traceIdDebounceRef.current = setTimeout(() => {
      setDebouncedTraceId(value)
      setPage(1)
    }, 400)
  }

  return (
    <main className="space-y-6">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs font-semibold tracking-widest text-(--color-accent) uppercase">
          executions
        </span>
        <div className="h-px flex-1 bg-(--color-border)" />
        {data && <span className="font-mono text-xs text-(--color-muted)">{data.total} total</span>}
        <Link
          to="/new"
          className="rounded border border-(--color-accent)/40 bg-(--color-accent)/5 px-3 py-1.5 font-mono text-xs text-(--color-accent) transition-colors hover:bg-(--color-accent)/10"
        >
          New analysis →
        </Link>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4">
        <div className="w-44">
          <Select
            label="Status"
            options={STATUS_OPTIONS}
            value={statusFilter}
            onChange={handleStatusChange}
          />
        </div>
        <div className="min-w-48 flex-1">
          <Input
            label="Trace ID"
            placeholder="filter by trace id…"
            value={traceIdFilter}
            onChange={handleTraceIdChange}
          />
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-16">
          <Spinner />
        </div>
      )}

      {/* Error */}
      {error && !isLoading && (
        <div className="rounded-lg border border-(--color-error)/40 bg-(--color-error)/5 px-5 py-4">
          <p className="font-mono text-sm text-(--color-error)">
            <span className="font-semibold">Error: </span>
            {error.message}
          </p>
        </div>
      )}

      {/* Empty */}
      {!isLoading && !error && data && data.items.length === 0 && (
        <div className="rounded-lg border border-(--color-border) bg-(--color-surface) px-6 py-12 text-center">
          <p className="font-mono text-sm text-(--color-muted)">No executions found.</p>
        </div>
      )}

      {/* Table */}
      {!isLoading && !error && data && data.items.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-(--color-border)">
          <table className="w-full font-mono text-sm">
            <thead>
              <tr className="border-b border-(--color-border) bg-(--color-surface)">
                <th className="px-4 py-3 text-left text-xs tracking-widest text-(--color-muted) uppercase">
                  Trace ID
                </th>
                <th className="px-4 py-3 text-left text-xs tracking-widest text-(--color-muted) uppercase">
                  Concern
                </th>
                <th className="hidden px-4 py-3 text-left text-xs tracking-widest text-(--color-muted) uppercase md:table-cell">
                  Started
                </th>
                <th className="px-4 py-3 text-left text-xs tracking-widest text-(--color-muted) uppercase">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr
                  key={item.trace_id}
                  onClick={() =>
                    navigate(item.status === 'done' ? `/jobs/${item.trace_id}/report` : `/jobs/${item.trace_id}`)
                  }
                  className="cursor-pointer border-b border-(--color-border) bg-(--color-surface) transition-colors duration-100 last:border-b-0 hover:bg-white/[0.03]"
                >
                  <td className="px-4 py-3">
                    <span className="font-mono text-(--color-text)" title={item.trace_id}>
                      {truncateTraceId(item.trace_id)}
                    </span>
                  </td>
                  <td className="max-w-xs px-4 py-3">
                    <span
                      className="block truncate text-xs text-(--color-muted)"
                      title={item.concern}
                    >
                      {item.concern}
                    </span>
                  </td>
                  <td className="hidden px-4 py-3 text-xs whitespace-nowrap text-(--color-muted) md:table-cell">
                    {formatDate(item.created_at)}
                  </td>
                  <td className="px-4 py-3">
                    <Badge status={item.status as JobStatus} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {!isLoading && !error && data && data.pages > 1 && (
        <div className="flex items-center justify-between pt-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={page <= 1 || isLoading}
            onClick={() => setPage((p) => p - 1)}
          >
            ← prev
          </Button>
          <span className="font-mono text-xs text-(--color-muted)">
            page {page} of {data.pages}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= data.pages || isLoading}
            onClick={() => setPage((p) => p + 1)}
          >
            next →
          </Button>
        </div>
      )}
    </main>
  )
}
