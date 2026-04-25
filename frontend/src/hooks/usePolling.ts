import { useCallback, useEffect, useRef, useState } from 'react'

export interface UsePollingResult<T> {
  data: T | null
  error: Error | null
  isPolling: boolean
  startPolling: () => void
}

export function usePolling<T>(
  fn: () => Promise<T>,
  interval: number,
  shouldStop: (data: T) => boolean,
): UsePollingResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [isPolling, setIsPolling] = useState(false)

  // Keep refs to the latest callbacks so the interval closure never goes stale.
  const fnRef = useRef(fn)
  const shouldStopRef = useRef(shouldStop)

  useEffect(() => {
    fnRef.current = fn
    shouldStopRef.current = shouldStop
  })

  const startPolling = useCallback(() => {
    setData(null)
    setError(null)
    setIsPolling(true)
  }, [])

  useEffect(() => {
    if (!isPolling) return

    let cancelled = false

    const tick = async () => {
      try {
        const result = await fnRef.current()
        if (cancelled) return
        setData(result)
        if (shouldStopRef.current(result)) {
          setIsPolling(false)
        }
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err : new Error(String(err)))
        setIsPolling(false)
      }
    }

    void tick()
    const id = setInterval(() => void tick(), interval)

    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [isPolling, interval])

  return { data, error, isPolling, startPolling }
}
