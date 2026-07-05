import type { PanelProps } from './types'
import type { DiscoveryArtifact } from '../../../api/types'

export function DiscoveryPanel({ results, artifacts }: PanelProps) {
  const artifact = artifacts.find((a) => a.node === 'discovery') as DiscoveryArtifact | undefined
  const meta = results?.discovery?.project_metadata
  const summary = results?.discovery?.discovery_summary
  const error = results?.discovery?.discovery_error
  const steps = artifact?.steps ?? []

  return (
    <div className="space-y-5">
      {/* Dep counts */}
      {meta && (
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1 rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
            <p className="font-mono text-2xl font-semibold text-[--color-text]">
              {meta.direct_dependencies_count}
            </p>
            <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Direct</p>
          </div>
          <div className="space-y-1 rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
            <p className="font-mono text-2xl font-semibold text-[--color-text]">
              {meta.transitive_dependencies_count}
            </p>
            <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Transitive</p>
          </div>
        </div>
      )}

      {/* Project metadata */}
      {meta && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 font-mono text-xs">
          <dt className="tracking-widest text-[--color-muted] uppercase">Name</dt>
          <dd className="text-[--color-text]">{meta.name}</dd>
          <dt className="tracking-widest text-[--color-muted] uppercase">Manager</dt>
          <dd className="text-[--color-text]">{meta.package_manager}</dd>
        </dl>
      )}

      {/* Pipeline steps */}
      {steps.length > 0 && (
        <div className="space-y-1">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Steps</p>
          <ul className="space-y-1">
            {steps.map((step) => (
              <li key={step} className="flex items-center gap-2 font-mono text-xs text-[--color-text]">
                <span className="text-[--color-done] text-xs">✓</span>
                {step}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Summary */}
      {summary && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Summary</p>
          <div className="rounded border border-[--color-border] bg-[--color-surface-raised] px-4 py-3">
            <p className="font-mono text-xs leading-relaxed text-[--color-text]">{summary}</p>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded border border-[--color-error]/30 bg-[--color-error]/5 px-4 py-3">
          <p className="font-mono text-xs text-[--color-error]">
            <span className="font-semibold">Error: </span>{error}
          </p>
        </div>
      )}
    </div>
  )
}
