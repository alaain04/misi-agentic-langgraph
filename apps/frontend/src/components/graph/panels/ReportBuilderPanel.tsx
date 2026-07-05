import { Link, useParams } from 'react-router-dom'
import type { PanelProps } from './types'
import type { ReportArtifact } from '../../../api/types'

export function ReportBuilderPanel({ artifacts }: PanelProps) {
  const { traceId } = useParams<{ traceId: string }>()
  const artifact = artifacts.find((a) => a.node === 'report_builder') as ReportArtifact | undefined
  const report = artifact?.output

  if (!report) {
    return <p className="font-mono text-xs text-[--color-muted]">Report not yet available.</p>
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-[--color-border] bg-[--color-surface-raised] p-4">
        <p className="mb-1 font-mono text-xs tracking-widest text-[--color-muted] uppercase">
          Overall risk
        </p>
        <p className="font-mono text-lg font-semibold uppercase text-[--color-text]">
          {report.overall_risk_level}
        </p>
        <p className="mt-1 font-mono text-xs text-[--color-muted]">
          {report.summary.total_deps} dependencies analysed
        </p>
      </div>

      {traceId && (
        <Link
          to={`/jobs/${traceId}/report`}
          className="block w-full rounded-lg border border-[--color-accent]/40 bg-[--color-accent]/5 px-4 py-3 text-center font-mono text-xs font-semibold text-[--color-accent] transition-colors hover:bg-[--color-accent]/10"
        >
          View full report →
        </Link>
      )}
    </div>
  )
}
