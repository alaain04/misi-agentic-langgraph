import type { PanelProps } from './types'
import type { ToolRunnerArtifact } from '../../../api/types'
import { nodeKind } from '../graphDefinition'

export function ToolPanel({ nodeId, artifacts }: PanelProps) {
  const kind = nodeKind(nodeId)
  if (kind.kind !== 'tool') return null

  const toolRunnerArt = artifacts.find(a => a.node === 'tool_runner') as ToolRunnerArtifact | undefined
  const iteration = toolRunnerArt?.iterations.find(i => i.conductor_iteration === kind.iter)
  const errorEntry = iteration?.errors.find(e => e.tool === kind.name)

  return (
    <div className="space-y-2">
      <div className="flex gap-4 font-mono text-xs">
        <span className="text-(--color-muted)">Tool</span>
        <span className="text-(--color-accent)">{kind.name}</span>
      </div>
      <div className="flex gap-4 font-mono text-xs">
        <span className="text-(--color-muted)">Conductor iteration</span>
        <span className="text-(--color-text)">{kind.iter}</span>
      </div>
      {errorEntry ? (
        <div className="rounded border border-(--color-error)/40 bg-(--color-error)/5 p-3">
          <p className="mb-1 font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">Error</p>
          <p className="font-mono text-xs text-(--color-error)">{errorEntry.error}</p>
        </div>
      ) : (
        <p className="font-mono text-xs text-(--color-muted)">Completed successfully.</p>
      )}
    </div>
  )
}
