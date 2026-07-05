import { useEffect } from 'react'
import type { NodeId } from './graphDefinition'
import { getPanelComponent } from './nodeRegistry'
import type { Artifact, JobResult } from '../../api/types'

interface NodeDetailPanelProps {
  nodeId: NodeId | null
  results: JobResult | null
  artifacts: Artifact[]
  onClose: () => void
}

export function NodeDetailPanel({ nodeId, results, artifacts, onClose }: NodeDetailPanelProps) {
  // Close on Escape key
  useEffect(() => {
    if (!nodeId) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [nodeId, onClose])

  if (!nodeId) return null

  const Panel = getPanelComponent(nodeId)

  return (
    <>
      {/* Transparent backdrop — clicking outside closes */}
      <div className="fixed inset-0 z-20" onClick={onClose} aria-hidden="true" />

      {/* Slide-in panel */}
      <div className="fixed top-0 right-0 bottom-0 z-30 flex w-full max-w-md flex-col border-l border-(--color-border) bg-(--color-surface) shadow-2xl">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-(--color-border) px-5 py-4">
          <span className="font-mono text-xs font-semibold tracking-widest text-(--color-accent) uppercase">
            node / {nodeId}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="font-mono text-xs text-(--color-muted) transition-colors hover:text-(--color-text)"
            aria-label="Close panel"
          >
            ✕
          </button>
        </div>

        {/* Panel content */}
        <div className="flex-1 overflow-y-auto p-5">
          {Panel ? (
            <Panel nodeId={nodeId} results={results} artifacts={artifacts} />
          ) : (
            <p className="font-mono text-xs text-(--color-muted)">No detail available for this node.</p>
          )}
        </div>
      </div>
    </>
  )
}
