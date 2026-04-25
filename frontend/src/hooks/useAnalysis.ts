import { useCallback, useRef, useState } from 'react'
import { submitAnalysis, getAnalysisStatus } from '../api/analyze'
import { usePolling } from './usePolling'
import type { AnalysisRequest, DiscoveryResult, JobStatus, StatusResponse } from '../api/types'

interface UseAnalysisResult {
  submit: (req: AnalysisRequest) => Promise<void>
  traceId: string | null
  status: JobStatus | null
  result: DiscoveryResult | null
  isLoading: boolean
  error: Error | null
}

function isTerminal(data: StatusResponse): boolean {
  return data.status === 'done' || data.status === 'failed'
}

export function useAnalysis(): UseAnalysisResult {
  const [traceId, setTraceId] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<Error | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const traceIdRef = useRef<string | null>(null)

  const pollFn = useCallback(async (): Promise<StatusResponse> => {
    if (!traceIdRef.current) {
      throw new Error('No trace ID available')
    }
    return getAnalysisStatus(traceIdRef.current)
  }, [])

  const { data, error: pollError, isPolling, startPolling } = usePolling(pollFn, 2000, isTerminal)

  const submit = useCallback(
    async (req: AnalysisRequest) => {
      setIsSubmitting(true)
      setSubmitError(null)
      try {
        const response = await submitAnalysis(req)
        traceIdRef.current = response.trace_id
        setTraceId(response.trace_id)
        startPolling()
      } catch (err) {
        setSubmitError(err instanceof Error ? err : new Error(String(err)))
      } finally {
        setIsSubmitting(false)
      }
    },
    [startPolling],
  )

  const isLoading = isSubmitting || isPolling
  const error = submitError ?? pollError
  const status = data?.status ?? null
  const result: DiscoveryResult | null = data?.result ?? null

  return { submit, traceId, status, result, isLoading, error }
}
