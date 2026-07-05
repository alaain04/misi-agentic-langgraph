// src/hooks/useJobStatus.ts
import { useCallback } from 'react'
import { getAnalysisStatus } from '../api/analyze'
import { usePolling } from './usePolling'
import type { StatusResponse } from '../api/types'

export function useJobStatus(traceId: string | undefined) {
  const pollFn = useCallback((): Promise<StatusResponse> => {
    if (!traceId) return Promise.reject(new Error('No trace ID'))
    return getAnalysisStatus(traceId)
  }, [traceId])

  const shouldStop = useCallback((data: StatusResponse): boolean => {
    return (
      data.status === 'done' ||
      data.status === 'failed' ||
      data.status === 'cancelled' ||
      data.status === 'awaiting_approval'
    )
  }, [])

  const { data, error, isPolling, startPolling, resumePolling } = usePolling<StatusResponse>(
    pollFn,
    2000,
    shouldStop,
  )

  return { data, error, isPolling, startPolling, resume: resumePolling }
}
