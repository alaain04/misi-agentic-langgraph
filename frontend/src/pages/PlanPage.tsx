import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { getJobStatus } from '../api/client'
import { sendChatMessage } from '../api/analyze'
import type { StatusResponse } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { Spinner } from '../components/ui/Spinner'
import { Button } from '../components/ui/Button'
import { cn } from '../lib/utils'

const SUBGRAPH_LABELS: Record<string, { label: string; description: string }> = {
  registry: {
    label: 'Registry',
    description: 'Check npm registry for outdated versions and vulnerability advisories',
  },
  repo: {
    label: 'Repository',
    description: 'Analyze GitHub repository health (stars, issues, last commit)',
  },
  runtime: {
    label: 'Runtime',
    description: 'Verify runtime compatibility and environment configuration',
  },
  risk_score: {
    label: 'Risk Score',
    description: 'Compute a composite risk score from all available signals',
  },
  recommendation: {
    label: 'Recommendations',
    description: 'Generate actionable remediation recommendations',
  },
}

type AiMessage = { role: 'ai'; text: string; plan?: string[] }
type UserMessage = { role: 'user'; text: string }
type ChatMessage = AiMessage | UserMessage

export default function PlanPage() {
  const { traceId } = useParams<{ traceId: string }>()
  const navigate = useNavigate()

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isApproved, setIsApproved] = useState(false)
  const [isCancelled, setIsCancelled] = useState(false)

  const lastAssistantMessageRef = useRef<string | null>(null)
  const hadApprovalRef = useRef(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const shouldStopPolling = useCallback((data: StatusResponse): boolean => {
    if (data.status === 'awaiting_approval') {
      hadApprovalRef.current = true
      return true
    }
    if (data.status === 'running' && hadApprovalRef.current) {
      return true
    }
    return data.status === 'done' || data.status === 'failed' || data.status === 'cancelled'
  }, [])

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

  // When awaiting_approval, push AI message (deduplicated by assistant_message content)
  useEffect(() => {
    if (!data || data.status !== 'awaiting_approval') return
    const assistantMessage = data.assistant_message ?? ''
    if (!assistantMessage) return
    if (assistantMessage === lastAssistantMessageRef.current) return
    lastAssistantMessageRef.current = assistantMessage
    setMessages((prev) => [
      ...prev,
      {
        role: 'ai',
        text: assistantMessage,
        plan: data.results?.plan,
      },
    ])
  }, [data])

  // Detect approval: when status becomes 'running' after 'awaiting_approval'
  useEffect(() => {
    if (!data || !hadApprovalRef.current) return
    if (data.status === 'running' && !isApproved) {
      setIsApproved(true)
      setMessages((prev) => [
        ...prev,
        {
          role: 'ai',
          text: 'Plan approved! Execution has started. You will be redirected to the execution detail page in 5 seconds…',
        },
      ])
    }
  }, [data, isApproved])

  // Redirect 5 seconds after approval
  useEffect(() => {
    if (!isApproved || !traceId) return
    const timer = setTimeout(() => {
      navigate(`/jobs/${traceId}`, { replace: true })
    }, 5000)
    return () => clearTimeout(timer)
  }, [isApproved, navigate, traceId])

  // Detect cancellation: when status becomes 'cancelled' after 'awaiting_approval'
  useEffect(() => {
    if (!data || !hadApprovalRef.current) return
    if (data.status === 'cancelled' && !isCancelled) {
      setIsCancelled(true)
      setMessages((prev) => [
        ...prev,
        {
          role: 'ai',
          text: 'Analysis cancelled. You will be redirected to the job detail page in 5 seconds…',
        },
      ])
    }
  }, [data, isCancelled])

  // Redirect 5 seconds after cancellation
  useEffect(() => {
    if (!isCancelled || !traceId) return
    const timer = setTimeout(() => {
      navigate(`/jobs/${traceId}`, { replace: true })
    }, 5000)
    return () => clearTimeout(timer)
  }, [isCancelled, navigate, traceId])

  // Redirect to execution detail once the job moves past planning (fallback)
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

  const isDisabled = isSubmitting || isPolling

  const handleSend = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || !traceId) return
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', text: trimmed }])
    setIsSubmitting(true)
    try {
      await sendChatMessage(traceId, trimmed)
      resumePolling()
    } finally {
      setIsSubmitting(false)
    }
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
        {traceId && <span className="font-mono text-xs text-[--color-muted]">{traceId}</span>}
      </div>

      {/* Chat window */}
      <div className="rounded-lg border border-[--color-border] bg-[--color-surface]">
        {/* Scrollable messages area */}
        <div className="max-h-[420px] space-y-5 overflow-y-auto p-4">
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

            // AI message bubble
            const isLatest = i === messages.length - 1
            return (
              <div key={i} className={cn('space-y-3', !isLatest && 'opacity-50')}>
                <div className="rounded-lg border border-[--color-border] bg-[--color-surface-raised] px-4 py-3">
                  <p className="font-mono text-xs leading-relaxed whitespace-pre-wrap text-[--color-text]">
                    {msg.text}
                  </p>
                  {msg.plan && msg.plan.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1.5 border-t border-[--color-border] pt-3">
                      {msg.plan.map((id) => (
                        <span
                          key={id}
                          className="inline-flex items-center rounded border border-[--color-border] bg-[--color-surface] px-2 py-0.5 font-mono text-[10px] text-[--color-muted]"
                        >
                          {SUBGRAPH_LABELS[id]?.label ?? id}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
          {/* Polling / thinking indicator */}
          {isPolling && (
            <div className="flex items-center gap-2 font-mono text-xs text-[--color-muted]">
              <Spinner size="sm" />
              {messages.length === 0 ? 'Creating the first plan…' : 'Agent is thinking…'}
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
        {/* Input area — always visible, disabled while polling/submitting */}
        <div className="space-y-3 border-t border-[--color-border] p-4">
          <div className="flex h-12 items-stretch gap-2">
            <textarea
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isDisabled}
              placeholder="Type your response… (Enter to send, Shift+Enter for newline)"
              className={cn(
                'h-full flex-1 resize-none rounded border border-[--color-border] bg-[--color-surface-raised]',
                'px-3 py-2 font-mono text-xs text-[--color-text] placeholder:text-[--color-muted]/40',
                'transition-colors focus:border-[--color-accent] focus:outline-none',
                'disabled:cursor-not-allowed disabled:opacity-40',
              )}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void handleSend(input)
                }
              }}
            />
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void handleSend(input)}
              disabled={!input.trim() || isDisabled}
              className="h-full"
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

          {/* Quick-action shortcuts — only when awaiting user input */}
          {!isDisabled && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
                quick actions:
              </span>
              <button
                type="button"
                onClick={() => void handleSend('Yes, proceed with the plan')}
                className="rounded border border-[--badge-done-border] px-2.5 py-1 font-mono text-[10px] text-[--badge-done-text] transition-colors hover:bg-[--badge-done-bg]"
              >
                Yes, proceed
              </button>
              <button
                type="button"
                onClick={() => void handleSend('Cancel this analysis')}
                className="rounded border border-[--badge-failed-border] px-2.5 py-1 font-mono text-[10px] text-[--badge-failed-text] transition-colors hover:bg-[--badge-failed-bg]"
              >
                Cancel analysis
              </button>
            </div>
          )}
        </div>
      </div>
    </main>
  )
}
