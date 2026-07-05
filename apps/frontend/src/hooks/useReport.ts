// src/hooks/useReport.ts
import { useEffect, useState } from 'react'
import { getAnalysisStatus } from '../api/analyze'
import type { AnalysisReport } from '../api/types'

export function useReport(traceId: string | undefined) {
  const [report, setReport] = useState<AnalysisReport | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    if (!traceId) return
    setIsLoading(true)
    getAnalysisStatus(traceId)
      .then((data) => {
        setReport(data.results?.analysis_report ?? null)
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err : new Error(String(err)))
      })
      .finally(() => setIsLoading(false))
  }, [traceId])

  return { report, isLoading, error }
}
