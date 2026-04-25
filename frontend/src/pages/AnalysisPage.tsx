import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { submitAnalysis } from '../api/analyze'
import { AnalysisForm } from '../components/analysis/AnalysisForm'
import type { AnalysisRequest } from '../api/types'
import type { Sample } from '../data/samples'

interface LocationState {
  sample?: Sample
}

export function AnalysisPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const state = location.state as LocationState | null
  const initialSample = state?.sample ?? null

  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function handleSubmit(req: AnalysisRequest) {
    setIsLoading(true)
    setError(null)
    try {
      const response = await submitAnalysis(req)
      navigate(`/jobs/${response.trace_id}/plan`)
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <main className="space-y-6">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
          {initialSample ? initialSample.label : 'new scan'}
        </span>
        <div className="h-px flex-1 bg-[--color-border]" />
      </div>

      <AnalysisForm
        onSubmit={handleSubmit}
        isLoading={isLoading}
        initialRequest={initialSample?.request}
        initialSampleId={initialSample?.id}
      />

      {error && (
        <div className="rounded-lg border border-[--color-error]/40 bg-[--color-error]/5 px-5 py-4">
          <p className="font-mono text-sm text-[--color-error]">
            <span className="font-semibold">Error: </span>
            {error.message}
          </p>
        </div>
      )}
    </main>
  )
}
