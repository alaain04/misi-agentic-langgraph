import { useCallback, useRef, useState } from 'react'
import { submitAnalysis, getAnalysisStatus, approvePlan } from '../api/analyze'
import { usePolling } from './usePolling'
import type { AnalysisResult, AnalysisRequest, JobStatus, StatusResponse } from '../api/types'

interface UseAnalysisResult {
  submit: (req: AnalysisRequest) => Promise<void>
  approvePlan: (action: 'approve' | 'modify' | 'cancel' | 'refine', plan?: string[], feedback?: string) => Promise<void>
  traceId: string | null
  status: JobStatus | null
  result: AnalysisResult | null
  isLoading: boolean
  error: Error | null
}

function isTerminal(data: StatusResponse): boolean {
  return data.status === 'done' || data.status === 'failed' || data.status === 'awaiting_approval'
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

  const { data, error: pollError, isPolling, startPolling, resumePolling } = usePolling(pollFn, 2000, isTerminal)

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

  const handleApprovePlan = useCallback(
    async (action: 'approve' | 'modify' | 'cancel' | 'refine', plan?: string[], feedback?: string) => {
      if (!traceIdRef.current) return
      await approvePlan(traceIdRef.current, {
        action,
        ...(plan ? { plan } : {}),
        ...(feedback ? { feedback } : {}),
      })
      // Resume polling so we track the job continuing after the human decision.
      // For 'refine', the backend re-runs the planner and returns to awaiting_approval
      // with the updated plan, at which point polling will pause again.
      resumePolling()
    },
    [resumePolling],
  )

  const isLoading = isSubmitting || isPolling
  const error = submitError ?? pollError
  const status = data?.status ?? null
  const result: AnalysisResult | null = data?.results ?? null

  return { submit, approvePlan: handleApprovePlan, traceId, status, result, isLoading, error }
}
