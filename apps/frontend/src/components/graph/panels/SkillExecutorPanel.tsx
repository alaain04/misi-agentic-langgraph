import type { PanelProps } from './types'
import type { CollectorArtifact } from '../../../api/types'

export function SkillExecutorPanel({ artifacts }: PanelProps) {
  const collector = artifacts.find((a) => a.node === 'evidence_collector') as
    | CollectorArtifact
    | undefined
  const steps = collector?.steps ?? []

  if (steps.length === 0) {
    return <p className="font-mono text-xs text-[--color-muted]">No skills executed yet.</p>
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
          Skills executed
        </p>
        <span className="rounded-full border border-[--color-border] bg-[--color-surface-raised] px-2 py-0.5 font-mono text-[10px] text-[--color-text]">
          {steps.length}
        </span>
      </div>
      <ul className="space-y-1">
        {steps.map((step) => (
          <li key={step} className="flex items-center gap-2 font-mono text-xs text-[--color-text]">
            <span className="text-[10px] text-[--color-accent]">▸</span>
            {step}
          </li>
        ))}
      </ul>
    </div>
  )
}
