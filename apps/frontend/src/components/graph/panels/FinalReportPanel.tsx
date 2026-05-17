import { useState } from 'react'
import type { PanelProps } from './DiscoveryPanel'
import type { NodeId } from '../graphDefinition'

const FIELD_MAP: Partial<Record<NodeId, 'summary' | 'review' | 'recommendation'>> = {
  summarizer: 'summary',
  reviewer: 'review',
  recommender: 'recommendation',
}

export function FinalReportPanel({ results, nodeId, artifacts }: PanelProps) {
  const [copied, setCopied] = useState(false)

  // Prefer live artifact output, fall back to finalized results field.
  const artifact = artifacts?.find((a) => a.node === nodeId)
  const field = FIELD_MAP[nodeId]
  const text = artifact?.output ?? (field ? results?.[field] : undefined)

  if (!text) {
    return <p className="font-mono text-xs text-[--color-muted]">No data available.</p>
  }

  const handleCopy = () => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="relative">
      <pre className="max-h-[500px] overflow-auto rounded border border-[--color-border] bg-[--color-surface-raised] p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap text-[--color-text]">
        <code>{text}</code>
      </pre>
      <button
        type="button"
        className="absolute top-3 right-3 rounded border border-[--color-border] bg-[--color-surface] px-2 py-1 font-mono text-xs text-[--color-muted] transition-colors hover:border-[--color-accent] hover:text-[--color-text]"
        onClick={handleCopy}
      >
        {copied ? 'copied!' : 'copy'}
      </button>
    </div>
  )
}
