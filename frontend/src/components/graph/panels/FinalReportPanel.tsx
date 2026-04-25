import { useState } from 'react'
import type { PanelProps } from './DiscoveryPanel'

export function FinalReportPanel({ results }: PanelProps) {
  const [copied, setCopied] = useState(false)
  const report = results?.final_report

  if (!report) {
    return <p className="font-mono text-xs text-[--color-muted]">No report available.</p>
  }

  const handleCopy = () => {
    void navigator.clipboard.writeText(report).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="relative">
      <pre className="max-h-[500px] overflow-auto whitespace-pre-wrap rounded border border-[--color-border] bg-[--color-surface-raised] p-4 font-mono text-xs leading-relaxed text-[--color-text]">
        <code>{report}</code>
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
