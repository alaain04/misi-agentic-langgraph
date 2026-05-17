import type { Proposal } from '../../../api/types'
import type { PanelProps } from './DiscoveryPanel'

const INTENT_STYLES: Record<
  Proposal['user_intended_action'],
  { label: string; className: string }
> = {
  approve: {
    label: 'approved',
    className: 'border-green-500/30 bg-green-500/10 text-green-400',
  },
  change: {
    label: 'requested changes',
    className: 'border-amber-500/30 bg-amber-500/10 text-amber-400',
  },
  cancel: {
    label: 'cancelled',
    className: 'border-[--color-error]/30 bg-[--color-error]/10 text-[--color-error]',
  },
}

function ProposalCard({ proposal, index }: { proposal: Proposal; index: number }) {
  const intent = proposal.user_intended_action ? INTENT_STYLES[proposal.user_intended_action] : null
  const date = new Date(proposal.created_at).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

  return (
    <div className="space-y-3 rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <span className="font-mono text-xs font-semibold text-[--color-accent]">
          Iteration {index + 1}
        </span>
        <span className="font-mono text-xs text-[--color-muted]">{date}</span>
      </div>

      {/* Plan chips */}
      <div className="flex flex-wrap gap-1.5">
        {proposal.plan.map((name) => (
          <span
            key={name}
            className="rounded border border-[--color-accent]/30 bg-[--color-accent]/5 px-2 py-0.5 font-mono text-xs text-[--color-accent]"
          >
            {name}
          </span>
        ))}
      </div>

      {/* Assistant message */}
      <div className="rounded border border-[--color-border] bg-[--color-surface] px-3 py-2">
        <p className="mb-1 font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
          Assistant
        </p>
        <p className="font-mono text-xs leading-relaxed text-[--color-text]">
          {proposal.assistant_message}
        </p>
      </div>

      {/* User response */}
      <div className="rounded border border-[--color-border] bg-[--color-surface] px-3 py-2">
        <p className="mb-1 font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
          User
        </p>
        <p className="font-mono text-xs leading-relaxed text-[--color-text]">
          {proposal.user_response}
        </p>
      </div>

      {/* Intent badge */}
      {intent && (
        <div className="flex justify-end">
          <span
            className={`rounded border px-2 py-0.5 font-mono text-[10px] tracking-widest uppercase ${intent.className}`}
          >
            {intent.label}
          </span>
        </div>
      )}
    </div>
  )
}

export function PlannerPanel({ artifacts }: PanelProps) {
  const artifact = artifacts?.find((a) => a.node === 'orchestrator')
  const proposals = artifact?.proposals

  if (!proposals || proposals.length === 0) {
    return (
      <p className="font-mono text-xs text-[--color-muted]">No conversation history available.</p>
    )
  }

  return (
    <div className="space-y-4">
      <p className="font-mono text-xs text-[--color-muted]">
        {proposals.length} conversation turn{proposals.length !== 1 ? 's' : ''}
      </p>
      <div className="space-y-3">
        {proposals.map((proposal, i) => (
          <ProposalCard key={proposal.created_at} proposal={proposal} index={i} />
        ))}
      </div>
    </div>
  )
}
