// src/pages/ReportPage.tsx
import { useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { useReport } from '../hooks/useReport'
import { Spinner } from '../components/ui/Spinner'
import { cn } from '../lib/utils'
import type { ReportFinding, Severity } from '../api/types'

const SEVERITY_STYLES: Record<Severity, string> = {
  critical: 'text-red-400 border-red-500/50 bg-red-500/10',
  high:     'text-orange-400 border-orange-500/50 bg-orange-500/10',
  medium:   'text-yellow-400 border-yellow-500/50 bg-yellow-500/10',
  low:      'text-blue-400 border-blue-500/50 bg-blue-500/10',
  info:     'text-(--color-muted) border-(--color-border) bg-(--color-surface-raised)',
}

const OVERALL_SEVERITY_STYLES: Record<string, string> = {
  critical: 'text-red-400',
  high:     'text-orange-400',
  medium:   'text-yellow-400',
  low:      'text-blue-400',
  none:     'text-(--color-muted)',
}

function FindingRow({ finding }: { finding: ReportFinding }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border border-(--color-border) bg-(--color-surface)">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-4 px-5 py-4 text-left"
      >
        <span
          className={cn(
            'shrink-0 rounded border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase',
            SEVERITY_STYLES[finding.severity],
          )}
        >
          {finding.severity}
        </span>
        <span className="min-w-0 flex-1 font-mono text-sm font-semibold text-(--color-text)">
          {finding.dep_name}
        </span>
        <span className="font-mono text-xs text-(--color-muted)">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="space-y-4 border-t border-(--color-border) px-5 py-4">
          <p className="font-mono text-xs leading-relaxed text-(--color-muted)">{finding.description}</p>

          {finding.recommendation && (
            <div>
              <p className="mb-1 font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">
                Recommendation
              </p>
              <p className="font-mono text-xs text-(--color-text)">{finding.recommendation}</p>
            </div>
          )}

          {finding.evidence && finding.evidence.length > 0 && (
            <div>
              <p className="mb-2 font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">
                Evidence
              </p>
              <div className="space-y-2">
                {finding.evidence.map((ev, i) => (
                  <div key={i} className="rounded border border-(--color-border) bg-(--color-surface-raised) px-3 py-2">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="font-mono text-[10px] font-semibold text-(--color-accent)">{ev.tool}</span>
                      {ev.url && (
                        <a
                          href={ev.url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-mono text-[10px] text-(--color-muted) underline hover:text-(--color-accent)"
                        >
                          {ev.url}
                        </a>
                      )}
                    </div>
                    <p className="font-mono text-[10px] text-(--color-muted) break-all">{ev.log_snippet}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function ReportPage() {
  const { traceId } = useParams<{ traceId: string }>()
  const { report, isLoading, error } = useReport(traceId)

  if (isLoading) {
    return (
      <main className="flex items-center justify-center py-20">
        <Spinner size="lg" />
      </main>
    )
  }

  if (error) {
    return (
      <main className="space-y-4">
        <Link to={`/jobs/${traceId}`} className="font-mono text-xs tracking-widest text-(--color-muted) uppercase hover:text-(--color-accent)">
          ← Execution view
        </Link>
        <div className="rounded-lg border border-(--color-error)/40 bg-(--color-error)/5 px-5 py-4">
          <p className="font-mono text-sm text-(--color-error)">{error.message}</p>
        </div>
      </main>
    )
  }

  if (!report) return <Navigate to={`/jobs/${traceId}`} replace />

  const overallColor = OVERALL_SEVERITY_STYLES[report.overall_risk_level] ?? OVERALL_SEVERITY_STYLES.none

  const severityCounts = (['critical', 'high', 'medium', 'low'] as const).map((sev) => ({
    sev,
    count: report.findings.filter((f) => f.severity === sev).length,
  }))

  return (
    <main className="space-y-8 pb-16">
      {/* Breadcrumb */}
      <Link
        to={`/jobs/${traceId}`}
        className="font-mono text-xs tracking-widest text-(--color-muted) uppercase transition-colors hover:text-(--color-accent)"
      >
        ← Execution view
      </Link>

      {/* Report header */}
      <div className="space-y-2 rounded-xl border border-(--color-border) bg-(--color-surface) p-6">
        <div className="flex flex-wrap items-center gap-3">
          <span className={cn('font-mono text-xs font-bold uppercase tracking-widest', overallColor)}>
            Overall: {report.overall_risk_level}
          </span>
          <span className="font-mono text-xs text-(--color-muted)">
            {report.findings.length} findings · {new Date(report.generated_at).toLocaleString()}
          </span>
        </div>
        <p className="font-mono text-xs text-(--color-muted)">{report.concern}</p>
        {report.executive_summary && (
          <p className="mt-2 font-mono text-xs leading-relaxed text-(--color-muted)">{report.executive_summary}</p>
        )}
      </div>

      {/* Summary strip */}
      <div className="grid grid-cols-4 gap-3">
        {severityCounts.map(({ sev, count }) => (
          <div key={sev} className="rounded-lg border border-(--color-border) bg-(--color-surface) p-4 text-center">
            <p className={cn('font-mono text-2xl font-bold', SEVERITY_STYLES[sev].split(' ')[0])}>
              {count}
            </p>
            <p className="font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">{sev}</p>
          </div>
        ))}
      </div>

      {/* Findings */}
      {report.findings.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center gap-3">
            <span className="font-mono text-xs font-semibold tracking-widest text-(--color-accent) uppercase">
              Findings
            </span>
            <div className="h-px flex-1 bg-(--color-border)" />
          </div>
          <div className="space-y-2">
            {report.findings.map((f) => (
              <FindingRow key={f.dep_name} finding={f} />
            ))}
          </div>
        </section>
      )}

      {/* Recommendations */}
      {report.recommendations.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center gap-3">
            <span className="font-mono text-xs font-semibold tracking-widest text-(--color-accent) uppercase">
              Recommendations
            </span>
            <div className="h-px flex-1 bg-(--color-border)" />
          </div>
          <ol className="space-y-2">
            {report.recommendations.map((r, i) => (
              <li key={i} className="flex gap-3 font-mono text-xs text-(--color-text)">
                <span className="shrink-0 text-(--color-accent)">{i + 1}.</span>
                {r}
              </li>
            ))}
          </ol>
        </section>
      )}
    </main>
  )
}
