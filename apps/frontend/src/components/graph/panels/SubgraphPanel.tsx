import { useState } from 'react'
import type { PanelProps } from './DiscoveryPanel'

export function SubgraphPanel({ nodeId, results, artifacts }: PanelProps) {
  const [rawOpen, setRawOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  // Prefer live artifact result (available as soon as node completes),
  // fall back to the finalized subgraph_results entry.
  const artifact = artifacts?.find((a) => a.node === nodeId)
  const data: unknown =
    artifact?.result ?? results?.subgraph_results?.find((r) => r.subgraph === nodeId)?.data

  if (data === undefined || data === null) {
    return (
      <p className="font-mono text-xs text-[--color-muted]">
        No output recorded for this subgraph.
      </p>
    )
  }

  const formatted = JSON.stringify(data, null, 2)

  const handleCopy = () => {
    void navigator.clipboard.writeText(formatted).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 font-mono text-xs">
        <dt className="tracking-widest text-[--color-muted] uppercase">Subgraph</dt>
        <dd className="text-[--color-text]">{nodeId}</dd>
      </dl>

      <div className="space-y-2">
        <button
          type="button"
          onClick={() => setRawOpen((v) => !v)}
          className="flex items-center gap-2 font-mono text-xs tracking-widest text-[--color-muted] uppercase transition-colors hover:text-[--color-text]"
        >
          <span
            className="inline-block transition-transform duration-150"
            style={{ transform: rawOpen ? 'rotate(90deg)' : 'rotate(0deg)' }}
          >
            ▶
          </span>
          Output
        </button>

        {rawOpen && (
          <div className="relative">
            <pre className="max-h-[400px] overflow-auto rounded border border-[--color-border] bg-[--color-surface-raised] p-4 font-mono text-xs leading-relaxed text-[--color-text]">
              <code>{formatted}</code>
            </pre>
            <button
              type="button"
              className="absolute top-3 right-3 rounded border border-[--color-border] bg-[--color-surface] px-2 py-1 font-mono text-xs text-[--color-muted] transition-colors hover:border-[--color-accent] hover:text-[--color-text]"
              onClick={handleCopy}
            >
              {copied ? 'copied!' : 'copy'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
