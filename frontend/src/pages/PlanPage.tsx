import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { getJobStatus } from '../api/client'
import { approvePlan } from '../api/analyze'
import type { StatusResponse } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { Spinner } from '../components/ui/Spinner'
import { Button } from '../components/ui/Button'
import { cn } from '../lib/utils'

const SUBGRAPH_LABELS: Record<string, { label: string; description: string }> = {
  registry: { label: 'Registry', description: 'Check npm registry for outdated versions and vulnerability advisories' },
  repo: { label: 'Repository', description: 'Analyze GitHub repository health (stars, issues, last commit)' },
  runtime: { label: 'Runtime', description: 'Verify runtime compatibility and environment configuration' },
  risk_score: { label: 'Risk Score', description: 'Compute a composite risk score from all available signals' },
  recommendation: { label: 'Recommendations', description: 'Generate actionable remediation recommendations' },
}

type AiMessage = { role: 'ai'; plan: string[]; discoverySummary?: string }
type UserMessage = { role: 'user'; text: string }
type ChatMessage = AiMessage | UserMessage

function shouldStopPolling(data: StatusResponse): boolean {
  return (
    data.status === 'awaiting_approval' ||
    data.status === 'done' ||
    data.status === 'failed'
  )
}

export default function PlanPage() {
  const { traceId } = useParams<{ traceId: string }>()
  const navigate = useNavigate()

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [feedback, setFeedback] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [selectedPlan, setSelectedPlan] = useState<string[]>([])

  const lastPlanKeyRef = useRef<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const fetchStatus = useCallback((): Promise<StatusResponse> => {
    if (!traceId) return Promise.reject(new Error('No trace ID'))
    return getJobStatus(traceId)
  }, [traceId])

  const { data, error, isPolling, startPolling, resumePolling } = usePolling<StatusResponse>(
    fetchStatus,
    2000,
    shouldStopPolling,
  )

  useEffect(() => {
    startPolling()
  }, [startPolling])

  // When awaiting_approval, push AI message with the plan (deduplicated by plan contents)
  useEffect(() => {
    if (!data || data.status !== 'awaiting_approval') return
    const plan = data.results?.plan ?? []
    const planKey = JSON.stringify(plan)
    if (planKey === lastPlanKeyRef.current) return
    lastPlanKeyRef.current = planKey
    setSelectedPlan(plan)
    setMessages((prev) => [
      ...prev,
      {
        role: 'ai',
        plan,
        discoverySummary: data.results?.discovery?.discovery_summary,
      },
    ])
  }, [data])

  // Redirect to execution detail once the job moves past planning
  useEffect(() => {
    if (!data) return
    if (data.status === 'done' || data.status === 'failed') {
      navigate(`/jobs/${traceId}`, { replace: true })
    }
  }, [data, navigate, traceId])

  // Scroll to bottom on new messages or polling change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isPolling])

  const isAwaitingApproval = data?.status === 'awaiting_approval' && !isPolling

  const handleSendFeedback = async () => {
    if (!feedback.trim() || !traceId) return
    const text = feedback.trim()
    setFeedback('')
    setMessages((prev) => [...prev, { role: 'user', text }])
    setIsSubmitting(true)
    try {
      await approvePlan(traceId, { action: 'refine', feedback: text })
      resumePolling()
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleApprove = async () => {
    if (!traceId) return
    setIsSubmitting(true)
    try {
      const latestAiPlan = [...messages].reverse().find((m): m is AiMessage => m.role === 'ai')?.plan ?? []
      const originalSet = new Set(latestAiPlan)
      const selectedSet = new Set(selectedPlan)
      const isModified =
        selectedSet.size !== originalSet.size || [...selectedSet].some((id) => !originalSet.has(id))
      if (isModified) {
        await approvePlan(traceId, { action: 'modify', plan: selectedPlan })
      } else {
        await approvePlan(traceId, { action: 'approve' })
      }
      navigate(`/jobs/${traceId}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleCancel = async () => {
    if (!traceId) return
    setIsSubmitting(true)
    try {
      await approvePlan(traceId, { action: 'cancel' })
      navigate('/jobs')
    } finally {
      setIsSubmitting(false)
    }
  }

  const toggleStep = (id: string) => {
    setSelectedPlan((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  return (
    <main className="space-y-6">
      <div>
        <Link
          to="/jobs"
          className="font-mono text-xs tracking-widest text-[--color-muted] uppercase transition-colors hover:text-[--color-accent]"
        >
          ← back to executions
        </Link>
      </div>

      {/* Header */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
          02 / plan review
        </span>
        <div className="h-px flex-1 bg-[--color-border]" />
        {traceId && (
          <span className="font-mono text-xs text-[--color-muted]">{traceId}</span>
        )}
      </div>

      {/* Chat window */}
      <div className="min-h-48 space-y-5 rounded-lg border border-[--color-border] bg-[--color-surface] p-5">
        {/* Empty state before first message */}
        {messages.length === 0 && !isPolling && !error && (
          <p className="py-8 text-center font-mono text-xs text-[--color-muted]">
            Waiting for the planner…
          </p>
        )}

        {/* Messages */}
        {messages.map((msg, i) => {
          if (msg.role === 'user') {
            return (
              <div key={i} className="flex justify-end">
                <div className="max-w-lg rounded-lg border border-[--color-border] bg-[--color-surface-raised] px-4 py-3">
                  <p className="font-mono text-xs text-[--color-text]">{msg.text}</p>
                </div>
              </div>
            )
          }

          // AI plan message
          const isLatest = i === messages.length - 1
          return (
            <div key={i} className={cn('space-y-4', !isLatest && 'opacity-50')}>
              <div className="flex items-center gap-2">
                <span className="font-mono text-[10px] font-semibold tracking-widest text-[--color-accent] uppercase">
                  planner
                </span>
                <div className="h-px flex-1 bg-[--color-border]" />
              </div>

              {msg.discoverySummary && (
                <blockquote className="border-l-2 border-[--color-accent]/40 pl-4">
                  <p className="font-mono text-xs italic leading-relaxed text-[--color-muted]">
                    {msg.discoverySummary}
                  </p>
                </blockquote>
              )}

              <div className="space-y-2">
                <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
                  Suggested steps
                </p>
                <ul className="space-y-1.5">
                  {msg.plan.map((id) => {
                    const meta = SUBGRAPH_LABELS[id]
                    const checked = selectedPlan.includes(id)
                    return (
                      <li key={id}>
                        <label
                          className={cn(
                            'flex cursor-pointer items-start gap-3 rounded border px-3 py-2.5 transition-colors',
                            isLatest
                              ? checked
                                ? 'border-[--color-accent]/40 bg-[--color-surface-raised]'
                                : 'border-[--color-border] bg-[--color-surface-raised] opacity-60 hover:opacity-90'
                              : 'border-[--color-border] bg-[--color-surface-raised]',
                            !isLatest && 'cursor-default',
                          )}
                        >
                          {isLatest && (
                            <input
                              type="checkbox"
                              className="mt-0.5 accent-[--color-accent]"
                              checked={checked}
                              onChange={() => toggleStep(id)}
                              disabled={isSubmitting}
                            />
                          )}
                          <div className="space-y-0.5">
                            <span className="font-mono text-xs font-medium text-[--color-text]">
                              {meta?.label ?? id}
                            </span>
                            {meta?.description && (
                              <p className="font-mono text-[10px] text-[--color-muted]">
                                {meta.description}
                              </p>
                            )}
                          </div>
                        </label>
                      </li>
                    )
                  })}
                </ul>
              </div>
            </div>
          )
        })}

        {/* Polling / thinking indicator */}
        {isPolling && (
          <div className="flex items-center gap-2 font-mono text-xs text-[--color-muted]">
            <Spinner size="sm" />
            {messages.length === 0 ? 'Running discovery…' : 'Agent is thinking…'}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded border border-[--color-error]/40 bg-[--color-error]/5 px-4 py-3">
            <p className="font-mono text-xs text-[--color-error]">{error.message}</p>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Interaction area — shown only when awaiting approval */}
      {isAwaitingApproval && (
        <div className="space-y-4 rounded-lg border border-[--badge-awaiting-border] bg-[--color-surface] p-5">
          {/* Feedback */}
          <div className="space-y-2">
            <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
              Refine with feedback
            </p>
            <div className="flex items-end gap-2">
              <textarea
                rows={2}
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                disabled={isSubmitting}
                placeholder="e.g. skip the runtime check, focus only on security vulnerabilities…"
                className={cn(
                  'flex-1 resize-none rounded border border-[--color-border] bg-[--color-surface-raised]',
                  'px-3 py-2 font-mono text-xs text-[--color-text] placeholder:text-[--color-muted]/40',
                  'transition-colors focus:border-[--badge-awaiting-border] focus:outline-none',
                  'disabled:cursor-not-allowed disabled:opacity-40',
                )}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void handleSendFeedback()
                  }
                }}
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void handleSendFeedback()}
                disabled={!feedback.trim() || isSubmitting}
                className="shrink-0"
              >
                {isSubmitting ? (
                  <>
                    <span className="size-3 animate-spin rounded-full border border-[--color-accent] border-t-transparent" />
                    Sending…
                  </>
                ) : (
                  'Send'
                )}
              </Button>
            </div>
          </div>

          {/* Approve / Cancel */}
          <div className="flex items-center justify-between border-t border-[--color-border] pt-4">
            <span className="font-mono text-xs text-[--color-muted]">
              {selectedPlan.length} step{selectedPlan.length !== 1 ? 's' : ''} selected
            </span>
            <div className="flex items-center gap-3">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void handleCancel()}
                disabled={isSubmitting}
                className="text-[--color-error] hover:bg-[--color-error]/10 hover:text-[--color-error]"
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={() => void handleApprove()}
                disabled={selectedPlan.length === 0 || isSubmitting}
              >
                {isSubmitting ? (
                  <>
                    <span className="size-3 animate-spin rounded-full border border-[--color-bg] border-t-transparent" />
                    Submitting…
                  </>
                ) : (
                  'Run Analysis'
                )}
              </Button>
            </div>
          </div>
        </div>
      )}
    </main>
  )
}
