import { useAnalysis } from '../hooks/useAnalysis'
import { AnalysisForm } from '../components/analysis/AnalysisForm'
import { AnalysisStatus } from '../components/analysis/AnalysisStatus'
import { AnalysisResult } from '../components/analysis/AnalysisResult'

export function AnalysisPage() {
  const { submit, traceId, status, result, isLoading, error } = useAnalysis()

  return (
    <main className="space-y-6">
      <AnalysisForm onSubmit={submit} isLoading={isLoading} />

      {error && (
        <div className="rounded-lg border border-[--color-error]/40 bg-[--color-error]/5 px-5 py-4">
          <p className="font-mono text-sm text-[--color-error]">
            <span className="font-semibold">Error: </span>
            {error.message}
          </p>
        </div>
      )}

      {traceId && status && <AnalysisStatus traceId={traceId} status={status} />}

      {status && (status === 'done' || status === 'failed') && (
        <AnalysisResult status={status} result={result ?? undefined} />
      )}
    </main>
  )
}
