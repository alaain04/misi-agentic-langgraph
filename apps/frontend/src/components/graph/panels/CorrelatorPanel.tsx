import type { PanelProps } from './types'
import type { CorrelatorArtifact } from '../../../api/types'

export function CorrelatorPanel({ artifacts }: PanelProps) {
  const artifact = artifacts.find((a) => a.node === 'evidence_correlator') as
    | CorrelatorArtifact
    | undefined
  const data = artifact?.data

  if (!data) {
    return <p className="font-mono text-xs text-(--color-muted)">Correlation not yet complete.</p>
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-(--color-border) bg-(--color-surface-raised) p-4">
          <p className="font-mono text-2xl font-semibold text-(--color-text)">{data.findings_count}</p>
          <p className="font-mono text-xs tracking-widest text-(--color-muted) uppercase">Findings</p>
        </div>
        <div className="rounded-lg border border-(--color-border) bg-(--color-surface-raised) p-4">
          <p className="font-mono text-2xl font-semibold text-(--color-text)">{data.contradictions_count}</p>
          <p className="font-mono text-xs tracking-widest text-(--color-muted) uppercase">Contradictions</p>
        </div>
      </div>

      {data.deps_covered.length > 0 && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-(--color-muted) uppercase">
            Dependencies covered
          </p>
          <div className="flex flex-wrap gap-1.5">
            {data.deps_covered.map((dep) => (
              <span
                key={dep}
                className="inline-flex items-center rounded border border-(--color-border) bg-(--color-surface-raised) px-2 py-0.5 font-mono text-[10px] text-(--color-text)"
              >
                {dep}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
