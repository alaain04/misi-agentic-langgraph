import type { AnalysisResult } from '../../api/types'
import type { NodeId } from './graphDefinition'
import { getPanelComponent } from './nodeRegistry'

interface NodeDetailPanelProps {
  nodeId: NodeId | null
  results: AnalysisResult | undefined
  onClose: () => void
}

export function NodeDetailPanel({ nodeId, results, onClose }: NodeDetailPanelProps) {
  if (!nodeId) return null

  const Panel = getPanelComponent(nodeId)

  return (
    <div className="space-y-4 rounded-lg border border-[--color-border] bg-[--color-surface] p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
            node / {nodeId}
          </span>
          <div className="h-px flex-1 bg-[--color-border]" />
        </div>
        <button
          type="button"
          onClick={onClose}
          className="ml-4 shrink-0 font-mono text-xs text-[--color-muted] transition-colors hover:text-[--color-text]"
          aria-label="Close panel"
        >
          ✕
        </button>
      </div>

      {/* Panel content */}
      {Panel ? (
        <Panel nodeId={nodeId} results={results} />
      ) : (
        <p className="font-mono text-xs text-[--color-muted]">
          No detail available for this node.
        </p>
      )}
    </div>
  )
}
