import { useState } from 'react'
import type { JobStatus, AnalysisResult } from '../../api/types'
import { DependencyTree } from './DependencyTree'

interface AnalysisResultProps {
  status: JobStatus
  result: AnalysisResult | undefined
}

export function AnalysisResult({ status, result }: AnalysisResultProps) {
  const [rawOpen, setRawOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  if (status === 'failed') {
    return (
      <div className="space-y-3 rounded-lg border border-[--color-error]/40 bg-[--color-error]/5 p-6">
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs font-semibold tracking-widest text-[--color-error] uppercase">
            03 / error
          </span>
          <div className="h-px flex-1 bg-[--color-error]/20" />
        </div>
        <p className="font-mono text-sm text-[--color-error]">
          The analysis pipeline failed. This may be due to an invalid repository, a network error,
          or an internal service fault. Check the backend logs for the trace ID above.
        </p>
      </div>
    )
  }

  if (status !== 'done') return null

  const discovery = result?.discovery
  const formatted = JSON.stringify(result, null, 2)

  const handleCopy = () => {
    void navigator.clipboard.writeText(formatted).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  const directCount = discovery?.project_metadata?.direct_dependencies_count ?? 0
  const transitiveCount = discovery?.transitive_dependencies?.length ?? 0

  return (
    <div className="space-y-6 rounded-lg border border-[--color-border] bg-[--color-surface] p-6">
      {/* Section label */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
          03 / result
        </span>
        <div className="h-px flex-1 bg-[--color-border]" />
      </div>

      {/* 1. Dependency counts */}
      {discovery?.project_metadata && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Dependencies
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1 rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
              <p className="font-mono text-2xl font-semibold text-[--color-text]">{directCount}</p>
              <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
                Direct
              </p>
            </div>
            <div className="space-y-1 rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
              <p className="font-mono text-2xl font-semibold text-[--color-text]">
                {transitiveCount}
              </p>
              <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
                Transitive
              </p>
            </div>
          </div>
        </div>
      )}

      {/* 2. Discovery summary */}
      {discovery?.discovery_summary && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Discovery Summary
          </p>
          <div className="rounded border border-[--color-border] bg-[--color-surface-raised] px-4 py-3">
            <p className="font-mono text-sm leading-relaxed text-[--color-text]">
              {discovery.discovery_summary}
            </p>
          </div>

          {discovery.discovery_error && (
            <div className="rounded border border-[--color-error]/30 bg-[--color-error]/5 px-4 py-3">
              <p className="font-mono text-xs leading-relaxed text-[--color-error]">
                <span className="font-semibold">Warning: </span>
                {discovery.discovery_error}
              </p>
            </div>
          )}
        </div>
      )}

      {/* 3. Dependency tree */}
      {discovery?.dependency_tree && Object.keys(discovery.dependency_tree).length > 0 && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Dependency Tree
          </p>
          <div className="overflow-hidden rounded border border-[--color-border] bg-[--color-surface-raised]">
            <DependencyTree
              data={discovery.dependency_tree}
              projectName={discovery.project_metadata?.name}
            />
          </div>
        </div>
      )}

      {/* 4. Raw JSON (collapsible) */}
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
          Raw JSON
        </button>

        {rawOpen && (
          <div className="relative">
            <pre className="max-h-[600px] overflow-x-auto overflow-y-auto rounded border border-[--color-border] bg-[--color-surface-raised] p-4 font-mono text-xs leading-relaxed text-[--color-text]">
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
