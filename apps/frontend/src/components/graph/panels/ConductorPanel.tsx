import type { PanelProps } from './types'
import type { ConductorArtifact } from '../../../api/types'
import { nodeKind } from '../graphDefinition'

export function ConductorPanel({ nodeId, artifacts }: PanelProps) {
  const conductorArt = artifacts.find(a => a.node === 'conductor') as ConductorArtifact | undefined
  if (!conductorArt?.iterations.length) {
    return <p className="font-mono text-xs text-(--color-muted)">No data yet.</p>
  }

  const kind = nodeKind(nodeId)
  const iters = conductorArt.iterations
  const iter = kind.kind === 'conductor'
    ? iters.find(i => i.iteration === kind.iter)
    : iters[iters.length - 1]

  if (!iter) return <p className="font-mono text-xs text-(--color-muted)">No data yet.</p>

  return (
    <div className="space-y-3">
      <div className="flex gap-4 font-mono text-xs">
        <span className="text-(--color-muted)">Iteration</span>
        <span className="text-(--color-text)">{iter.iteration}</span>
      </div>
      <div className="flex gap-4 font-mono text-xs">
        <span className="text-(--color-muted)">Findings</span>
        <span className="text-(--color-text)">{iter.findings_count}</span>
      </div>
      {iter.reasoning && (
        <div className="rounded border border-(--color-border) bg-(--color-surface-raised) p-3">
          <p className="mb-1 font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">Reasoning</p>
          <p className="font-mono text-xs text-(--color-text) leading-relaxed">{iter.reasoning}</p>
        </div>
      )}
      {iter.tool_calls.length > 0 && (
        <div>
          <p className="mb-1 font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">Tool calls</p>
          <ul className="space-y-1">
            {iter.tool_calls.map((tc, i) => (
              <li key={i} className="font-mono text-xs text-(--color-text)">
                <span className="text-(--color-accent)">{tc.tool}</span>
                {Object.keys(tc.args).length > 0 && (
                  <span className="text-(--color-muted)"> ({JSON.stringify(tc.args)})</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
