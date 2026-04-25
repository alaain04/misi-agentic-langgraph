import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { submitAnalysis } from '../api/analyze'
import { AnalysisForm } from '../components/analysis/AnalysisForm'
import type { AnalysisRequest } from '../api/types'

export function AnalysisPage() {
  const navigate = useNavigate()
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
      <AnalysisForm onSubmit={handleSubmit} isLoading={isLoading} />

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
