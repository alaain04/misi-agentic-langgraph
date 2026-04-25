import type { PanelProps } from './DiscoveryPanel'

export function PlannerPanel({ results }: PanelProps) {
  const plan = results?.plan
  if (!plan || plan.length === 0) {
    return <p className="font-mono text-xs text-[--color-muted]">No plan data available.</p>
  }

  return (
    <div className="space-y-4">
      <p className="font-mono text-xs text-[--color-muted]">
        {plan.length} subgraph{plan.length !== 1 ? 's' : ''} selected for analysis
      </p>
      <ol className="space-y-2">
        {plan.map((name, i) => (
          <li key={name} className="flex items-center gap-3">
            <span className="font-mono text-xs text-[--color-muted] tabular-nums">{i + 1}.</span>
            <span className="rounded border border-[--color-accent]/30 bg-[--color-accent]/5 px-2 py-0.5 font-mono text-xs text-[--color-accent]">
              {name}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}
