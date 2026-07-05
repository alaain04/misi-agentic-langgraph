import type { PanelProps } from './types'
import type { ReviewerArtifact } from '../../../api/types'

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'text-red-400 border-red-500/40 bg-red-500/5',
  high:     'text-orange-400 border-orange-500/40 bg-orange-500/5',
  medium:   'text-yellow-400 border-yellow-500/40 bg-yellow-500/5',
  low:      'text-blue-400 border-blue-500/40 bg-blue-500/5',
  info:     'text-(--color-muted) border-(--color-border) bg-(--color-surface-raised)',
}

export function FindingReviewerPanel({ artifacts }: PanelProps) {
  const artifact = artifacts.find((a) => a.node === 'finding_reviewer') as
    | ReviewerArtifact
    | undefined
  const findings = artifact?.data?.risk_findings ?? []
  const messages = artifact?.messages ?? []

  return (
    <div className="space-y-5">
      {findings.length > 0 && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-(--color-muted) uppercase">
            Findings ({findings.length})
          </p>
          <ul className="space-y-2">
            {findings.map((f) => (
              <li
                key={f.dep_name}
                className="flex items-start gap-3 rounded border border-(--color-border) bg-(--color-surface-raised) p-3"
              >
                <span
                  className={[
                    'mt-0.5 shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase',
                    SEVERITY_COLORS[f.severity] ?? SEVERITY_COLORS.info,
                  ].join(' ')}
                >
                  {f.severity}
                </span>
                <div className="min-w-0">
                  <p className="font-mono text-xs font-semibold text-(--color-text)">{f.dep_name}</p>
                  <p className="font-mono text-[10px] text-(--color-muted)">
                    score: {f.risk_score.toFixed(1)}/10
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {messages.length > 0 && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-(--color-muted) uppercase">
            Review conversation
          </p>
          {messages.map((msg, i) => (
            <div key={i} className={msg.role === 'human' ? 'flex justify-end' : ''}>
              <div className="max-w-[85%] rounded border border-(--color-border) bg-(--color-surface-raised) px-3 py-2">
                <p className="font-mono text-xs leading-relaxed whitespace-pre-wrap text-(--color-text)">
                  {msg.content}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {!artifact && (
        <p className="font-mono text-xs text-(--color-muted)">No review data yet.</p>
      )}
    </div>
  )
}
