import { useEffect, useState } from 'react'
import { Button } from '../ui/Button'
import { cn } from '../../lib/utils'

interface SubgraphOption {
  id: string
  label: string
  description: string
}

const SUBGRAPH_OPTIONS: SubgraphOption[] = [
  {
    id: 'registry',
    label: 'Registry',
    description: 'Check npm registry for outdated versions and vulnerability advisories',
  },
  {
    id: 'repo',
    label: 'Repository',
    description: 'Analyze GitHub repository health (stars, issues, last commit)',
  },
  {
    id: 'runtime',
    label: 'Runtime',
    description: 'Verify runtime compatibility and environment configuration',
  },
  {
    id: 'risk_score',
    label: 'Risk Score',
    description: 'Compute a composite risk score from all available signals',
  },
  {
    id: 'recommendation',
    label: 'Recommendations',
    description: 'Generate actionable remediation recommendations',
  },
]

interface PlanApprovalProps {
  plan: string[]
  discoverySummary?: string
  onDecide: (action: 'approve' | 'modify' | 'cancel' | 'refine', plan?: string[], feedback?: string) => Promise<void>
}

export function PlanApproval({ plan, discoverySummary, onDecide }: PlanApprovalProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set(plan))
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [feedback, setFeedback] = useState('')

  // Re-sync checkboxes when the AI returns a refined plan
  useEffect(() => {
    setSelected(new Set(plan))
  }, [plan])

  const originalSet = new Set(plan)
  const isModified =
    selected.size !== originalSet.size ||
    [...selected].some((id) => !originalSet.has(id))

  const handleToggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const handleRun = async () => {
    setIsSubmitting(true)
    try {
      if (isModified) {
        await onDecide('modify', [...selected])
      } else {
        await onDecide('approve')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleCancel = async () => {
    setIsSubmitting(true)
    try {
      await onDecide('cancel')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleRefine = async () => {
    if (!feedback.trim()) return
    setIsSubmitting(true)
    try {
      await onDecide('refine', undefined, feedback.trim())
      setFeedback('')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="space-y-5 rounded-lg border border-[--badge-awaiting-border] bg-[--color-surface] p-6">
      {/* Section header */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs font-semibold tracking-widest text-[--badge-awaiting-text] uppercase">
          02 / plan review
        </span>
        <div className="h-px flex-1 bg-[--badge-awaiting-border]" />
      </div>

      {/* Title + subtext */}
      <div className="space-y-1">
        <h2 className="font-display text-lg font-bold text-[--color-text]">Review Analysis Plan</h2>
        <p className="font-mono text-xs text-[--color-muted]">
          The AI suggested the following analysis steps. Approve, adjust, or cancel.
        </p>
      </div>

      {/* Discovery summary */}
      {discoverySummary && (
        <blockquote className="border-l-2 border-[--badge-awaiting-border] pl-4">
          <p className="font-mono text-xs leading-relaxed text-[--color-muted] italic">
            {discoverySummary}
          </p>
        </blockquote>
      )}

      {/* Subgraph toggles */}
      <div className="space-y-2">
        <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
          Analysis Steps
        </p>
        <ul className="space-y-2">
          {SUBGRAPH_OPTIONS.map((opt) => {
            const isChecked = selected.has(opt.id)
            const isInPlan = originalSet.has(opt.id)
            return (
              <li key={opt.id}>
                <label
                  className={cn(
                    'flex cursor-pointer items-start gap-4 rounded border px-4 py-3 transition-all duration-150',
                    isChecked
                      ? 'border-[--badge-awaiting-border] bg-[--badge-awaiting-bg]'
                      : 'border-[--color-border] bg-[--color-surface-raised] opacity-60 hover:opacity-80',
                  )}
                >
                  {/* Custom checkbox */}
                  <span
                    className={cn(
                      'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-sm border transition-all duration-150',
                      isChecked
                        ? 'border-[--badge-awaiting-text] bg-[--badge-awaiting-text]'
                        : 'border-[--color-border] bg-transparent',
                    )}
                  >
                    {isChecked && (
                      <svg
                        viewBox="0 0 10 8"
                        fill="none"
                        className="size-2.5"
                        stroke="var(--color-bg)"
                        strokeWidth="1.8"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M1 4l3 3 5-6" />
                      </svg>
                    )}
                  </span>

                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={isChecked}
                    onChange={() => handleToggle(opt.id)}
                    disabled={isSubmitting}
                  />

                  <div className="min-w-0 flex-1 space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm font-medium text-[--color-text]">
                        {opt.label}
                      </span>
                      {isInPlan && (
                        <span className="font-mono text-[10px] tracking-widest text-[--badge-awaiting-text] uppercase">
                          suggested
                        </span>
                      )}
                    </div>
                    <p className="font-mono text-xs text-[--color-muted]">{opt.description}</p>
                  </div>
                </label>
              </li>
            )
          })}
        </ul>
      </div>

      {/* Feedback section */}
      <div className="space-y-3 border-t border-[--color-border] pt-4">
        <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
          Refine with feedback
        </p>
        <p className="font-mono text-xs text-[--color-muted]/70">
          Describe changes to the plan and let the AI revise it before you approve.
        </p>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <textarea
            rows={3}
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            disabled={isSubmitting}
            placeholder="e.g. skip the runtime check, focus only on security vulnerabilities…"
            className={cn(
              'min-w-0 flex-1 rounded border border-[--color-border] bg-[--color-surface-raised]',
              'px-3 py-2 font-mono text-xs text-[--color-text] placeholder:text-[--color-muted]/40',
              'resize-y transition-colors duration-150',
              'focus:border-[--badge-awaiting-border] focus:ring-1 focus:ring-[--badge-awaiting-border]/40 focus:outline-none',
              'disabled:cursor-not-allowed disabled:opacity-40',
            )}
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void handleRefine()}
            disabled={feedback.trim() === '' || isSubmitting}
            className="shrink-0 self-end sm:self-auto"
          >
            {isSubmitting ? (
              <>
                <span className="size-3 animate-spin rounded-full border border-[--color-accent] border-t-transparent" />
                Refining…
              </>
            ) : (
              'Refine Plan'
            )}
          </Button>
        </div>
      </div>

      {/* Action bar */}
      <div className="flex items-center justify-between gap-4 border-t border-[--color-border] pt-4">
        <span className="font-mono text-xs text-[--color-muted]">
          {selected.size} step{selected.size !== 1 ? 's' : ''} selected
          {isModified && (
            <span className="ml-2 text-[--badge-awaiting-text]">(modified from suggestion)</span>
          )}
        </span>

        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void handleCancel()}
            disabled={isSubmitting}
            className="text-[--color-error] hover:text-[--color-error] hover:bg-[--color-error]/10"
          >
            Cancel
          </Button>

          <Button
            variant="primary"
            size="sm"
            onClick={() => void handleRun()}
            disabled={selected.size === 0 || isSubmitting}
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
  )
}
