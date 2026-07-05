// src/pages/ExecutionPage.tsx
import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useJobStatus } from '../hooks/useJobStatus'
import { useChat } from '../hooks/useChat'
import { mapResponseToGraphState } from '../components/graph/graphStateMapper'
import { ExecutionGraph } from '../components/graph/ExecutionGraph'
import { NodeDetailPanel } from '../components/graph/NodeDetailPanel'
import { ChatOverlay } from '../components/chat/ChatOverlay'
import { Badge } from '../components/ui/Badge'
import { Spinner } from '../components/ui/Spinner'
import { cn } from '../lib/utils'
import type { NodeId } from '../components/graph/graphDefinition'

export default function ExecutionPage() {
  const { traceId } = useParams<{ traceId: string }>()

  const autopilot = localStorage.getItem('deprisk.autopilot') === 'true'
  const [chatOpen, setChatOpen] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<NodeId | null>(null)
  const [metaExpanded, setMetaExpanded] = useState(false)

  const { data, isPolling, error, startPolling, resume } = useJobStatus(traceId)

  const { activeGate, messages, send, isSending, sendError } = useChat(
    traceId,
    data?.artifacts ?? [],
    { autopilot, onSent: resume },
  )

  // Start polling on mount
  useEffect(() => { startPolling() }, [startPolling])

  // Auto-open chat overlay when a gate becomes active (and autopilot is off)
  useEffect(() => {
    if (activeGate && !autopilot) setChatOpen(true)
  }, [activeGate, autopilot])

  const renderData = mapResponseToGraphState(data)
  const status = data?.status ?? null
  const concern = data?.metadata?.concern ?? ''

  const handleNodeClick = useCallback((id: NodeId | null) => setSelectedNodeId(id), [])

  const chatDotClass = activeGate
    ? 'bg-(--color-accent) animate-pulse'
    : 'bg-(--color-muted)'

  return (
    <main className="flex flex-col gap-4">
      {/* Header bar */}
      <div className="flex items-center gap-3">
        <Link
          to="/jobs"
          className="shrink-0 font-mono text-xs tracking-widest text-(--color-muted) uppercase transition-colors hover:text-(--color-accent)"
        >
          ← Executions
        </Link>
        <div className="h-px flex-1 bg-(--color-border)" />
        {concern && (
          <span
            className="max-w-xs truncate font-mono text-xs text-(--color-muted)"
            title={concern}
          >
            {concern}
          </span>
        )}
        {status && <Badge status={status} />}
        {isPolling && <Spinner size="sm" />}

        {/* Chat button */}
        {!autopilot ? (
          <button
            type="button"
            onClick={() => setChatOpen(true)}
            className="flex items-center gap-1.5 rounded border border-(--color-border) bg-(--color-surface) px-3 py-1.5 font-mono text-xs text-(--color-muted) transition-colors hover:border-(--color-accent)/40 hover:text-(--color-text)"
            title={activeGate ? 'Response required' : 'View conversation'}
          >
            <span className={cn('h-1.5 w-1.5 rounded-full', chatDotClass)} />
            Chat
          </button>
        ) : (
          <span className="rounded border border-(--color-accent)/30 bg-(--color-accent)/5 px-2.5 py-1 font-mono text-[10px] tracking-widest text-(--color-accent) uppercase">
            Autopilot
          </span>
        )}
      </div>

      {/* Meta strip (collapsible) */}
      {data && (
        <button
          type="button"
          onClick={() => setMetaExpanded((v) => !v)}
          className="w-full rounded-lg border border-(--color-border) bg-(--color-surface) px-4 py-2.5 text-left transition-colors hover:bg-(--color-surface-raised)"
        >
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">
              {metaExpanded ? '▾' : '▸'} Details
            </span>
            {!metaExpanded && (
              <span className="font-mono text-xs text-(--color-muted) truncate">
                {traceId}
              </span>
            )}
          </div>
          {metaExpanded && (
            <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 font-mono text-xs">
              <dt className="tracking-widest text-(--color-muted) uppercase">Trace ID</dt>
              <dd className="truncate text-(--color-text)">{traceId}</dd>
              <dt className="tracking-widest text-(--color-muted) uppercase">Repo</dt>
              <dd className="truncate text-(--color-text)">{data.metadata?.repo_url}</dd>
              {data.completed_at && (
                <>
                  <dt className="tracking-widest text-(--color-muted) uppercase">Completed</dt>
                  <dd className="text-(--color-text)">{new Date(data.completed_at).toLocaleString()}</dd>
                </>
              )}
            </dl>
          )}
        </button>
      )}

      {/* Loading state */}
      {!data && !error && (
        <div className="flex items-center justify-center py-20">
          <Spinner size="lg" />
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="rounded-lg border border-(--color-error)/40 bg-(--color-error)/5 px-5 py-4">
          <p className="font-mono text-sm text-(--color-error)">
            <span className="font-semibold">Error: </span>{error.message}
          </p>
        </div>
      )}

      {/* Execution graph */}
      {data && (
        <ExecutionGraph
          renderData={renderData}
          selectedNodeId={selectedNodeId}
          onNodeClick={handleNodeClick}
          isRunning={status === 'running' || status === 'processing'}
        />
      )}

      {/* Status footer */}
      {status === 'done' && (
        <div className="flex items-center justify-end rounded-lg border border-(--color-border) bg-(--color-surface) px-5 py-4">
          <Link
            to={`/jobs/${traceId}/report`}
            className="font-mono text-sm font-semibold text-(--color-accent) transition-colors hover:text-(--color-accent-hover)"
          >
            View full report →
          </Link>
        </div>
      )}
      {status === 'failed' && (
        <div className="rounded-lg border border-(--color-error)/40 bg-(--color-error)/5 px-5 py-4">
          <p className="font-mono text-sm text-(--color-error)">
            <span className="font-semibold">Analysis failed.</span>
            {data?.error && (
              <span className="mt-1 block font-mono text-xs opacity-80">{data.error}</span>
            )}
          </p>
        </div>
      )}
      {status === 'cancelled' && (
        <div className="rounded-lg border border-(--color-border) bg-(--color-surface) px-5 py-4">
          <p className="font-mono text-sm text-(--color-muted)">Analysis was cancelled.</p>
        </div>
      )}

      {/* Node detail panel (slide-in) */}
      {selectedNodeId && data && (
        <NodeDetailPanel
          nodeId={selectedNodeId}
          results={data.results}
          artifacts={data.artifacts}
          onClose={() => setSelectedNodeId(null)}
        />
      )}

      {/* Chat overlay */}
      <ChatOverlay
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        activeGate={activeGate}
        messages={messages}
        isSending={isSending}
        onSend={send}
        sendError={sendError}
      />
    </main>
  )
}
