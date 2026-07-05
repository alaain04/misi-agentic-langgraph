import type { PanelProps } from './types'
import type { PlannerArtifact } from '../../../api/types'

export function PlannerPanel({ artifacts }: PanelProps) {
  const artifact = artifacts.find((a) => a.node === 'investigation_planner') as
    | PlannerArtifact
    | undefined
  const messages = artifact?.messages ?? []
  const plan = artifact?.data?.plan

  return (
    <div className="space-y-5">
      {/* Chat transcript */}
      {messages.length > 0 && (
        <div className="space-y-3">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Conversation
          </p>
          {messages.map((msg, i) => (
            <div
              key={i}
              className={
                msg.role === 'human'
                  ? 'flex justify-end'
                  : ''
              }
            >
              <div
                className={[
                  'max-w-[85%] rounded border px-3 py-2',
                  msg.role === 'human'
                    ? 'border-[--color-border] bg-[--color-surface]'
                    : 'border-[--color-border] bg-[--color-surface-raised]',
                ].join(' ')}
              >
                <p className="font-mono text-xs leading-relaxed whitespace-pre-wrap text-[--color-text]">
                  {msg.content}
                </p>
                {msg.action && (
                  <p className="mt-1 font-mono text-[10px] text-[--color-accent]">
                    action: {msg.action}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Hypothesis list */}
      {plan && plan.hypotheses.length > 0 && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Hypotheses ({plan.hypotheses.length})
          </p>
          <ul className="space-y-2">
            {plan.hypotheses.map((h) => (
              <li
                key={h.id}
                className="rounded border border-[--color-border] bg-[--color-surface-raised] p-3"
              >
                <p className="mb-1 font-mono text-xs font-semibold text-[--color-text]">
                  {h.dep_name}
                </p>
                <p className="mb-2 font-mono text-xs leading-relaxed text-[--color-muted]">
                  {h.statement}
                </p>
                <div className="flex flex-wrap gap-1">
                  {h.skills.map((s) => (
                    <span
                      key={s}
                      className="inline-flex items-center rounded border border-[--color-border] bg-[--color-surface] px-2 py-0.5 font-mono text-[10px] text-[--color-muted]"
                    >
                      {s}
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!artifact && (
        <p className="font-mono text-xs text-[--color-muted]">No plan data yet.</p>
      )}
    </div>
  )
}
