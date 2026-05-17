import type { ArtifactInfo, AnalysisResult } from '../../../api/types'
import type { NodeId } from '../graphDefinition'
import { DependencyTree } from '../../analysis/DependencyTree'

export interface PanelProps {
  nodeId: NodeId
  results: AnalysisResult | undefined
  artifacts: ArtifactInfo[] | undefined
}

export function DiscoveryPanel({ results }: PanelProps) {
  const discovery = results?.discovery
  if (!discovery) {
    return <p className="font-mono text-xs text-[--color-muted]">No discovery data available.</p>
  }

  const meta = discovery.project_metadata
  const directCount = meta?.direct_dependencies_count ?? 0
  const transitiveCount = discovery.transitive_dependencies?.length ?? 0

  return (
    <div className="space-y-5">
      {/* Metadata + counts */}
      {meta && (
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
      )}

      {/* Project metadata */}
      {/* {meta && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 font-mono text-xs">
          <dt className="tracking-widest text-[--color-muted] uppercase">Name</dt>
          <dd className="text-[--color-text]">{meta.name}</dd>
          <dt className="tracking-widest text-[--color-muted] uppercase">Manager</dt>
          <dd className="text-[--color-text]">{meta.package_manager}</dd>
        </dl>
      )} */}

      {/* Manifest files */}
      {/* {discovery.manifest_files && discovery.manifest_files.length > 0 && (
        <div className="space-y-1">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">Manifest Files</p>
          <ul className="space-y-0.5">
            {discovery.manifest_files.map((f) => (
              <li key={f} className="font-mono text-xs text-[--color-text]">{f}</li>
            ))}
          </ul>
        </div>
      )} */}

      {/* Discovery summary */}
      {discovery.discovery_summary && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Summary
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

      {/* Dependency tree */}
      {discovery.dependency_tree && Object.keys(discovery.dependency_tree).length > 0 && (
        <div className="space-y-2">
          <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
            Dependency Tree
          </p>
          <div className="overflow-hidden rounded border border-[--color-border] bg-[--color-surface-raised]">
            <DependencyTree
              data={discovery.dependency_tree}
              projectName={meta?.name}
              height={300}
            />
          </div>
        </div>
      )}
    </div>
  )
}
