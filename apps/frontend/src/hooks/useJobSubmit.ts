// src/hooks/useJobSubmit.ts
import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { submitAnalysis } from '../api/analyze'
import type { AnalysisRequest } from '../api/types'

export function useJobSubmit() {
  const navigate = useNavigate()
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const submit = useCallback(
    async (req: AnalysisRequest) => {
      setIsSubmitting(true)
      setError(null)
      try {
        const res = await submitAnalysis(req)
        navigate(`/jobs/${res.trace_id}`)
      } catch (err) {
        setError(err instanceof Error ? err : new Error(String(err)))
      } finally {
        setIsSubmitting(false)
      }
    },
    [navigate],
  )

  return { submit, isSubmitting, error }
}
