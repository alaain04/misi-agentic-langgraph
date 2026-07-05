// src/hooks/useChat.ts
import { useCallback, useEffect, useRef, useState } from 'react'
import { sendChatMessage } from '../api/analyze'
import { getActiveGate } from '../lib/getActiveGate'
import type { Artifact, ArtifactMessage, PlannerArtifact, ReviewerArtifact } from '../api/types'
import type { Gate } from '../lib/getActiveGate'

interface UseChatOptions {
  autopilot: boolean
  onSent: () => void
}

export function useChat(
  traceId: string | undefined,
  artifacts: Artifact[],
  opts: UseChatOptions,
) {
  const [isSending, setIsSending] = useState(false)
  const hasFiredRef = useRef<Gate | null>(null)
  const optsRef = useRef(opts)
  useEffect(() => {
    optsRef.current = opts
  })

  const activeGate = getActiveGate(artifacts)

  const messages: ArtifactMessage[] = (() => {
    if (!activeGate) return []
    const artifact = artifacts.find((a) => a.node === activeGate) as
      | PlannerArtifact
      | ReviewerArtifact
      | undefined
    return artifact?.messages ?? []
  })()

  const send = useCallback(
    async (message: string) => {
      if (!traceId) return
      setIsSending(true)
      try {
        await sendChatMessage(traceId, message)
        optsRef.current.onSent()
      } finally {
        setIsSending(false)
      }
    },
    [traceId],
  )

  // Autopilot: auto-approve each gate once
  useEffect(() => {
    if (!optsRef.current.autopilot || !activeGate) return
    if (hasFiredRef.current === activeGate) return
    hasFiredRef.current = activeGate
    void send('Yes, proceed')
  }, [activeGate, send])

  // Reset fired ref when gate closes
  useEffect(() => {
    if (!activeGate) hasFiredRef.current = null
  }, [activeGate])

  return { activeGate, messages, send, isSending }
}
