// src/components/chat/ChatOverlay.tsx
import { useEffect, useRef, useState } from 'react'
import { marked } from 'marked'
import { cn } from '../../lib/utils'
import { Button } from '../ui/Button'
import { Spinner } from '../ui/Spinner'
import type { ArtifactMessage } from '../../api/types'
import type { Gate } from '../../lib/getActiveGate'

interface ChatOverlayProps {
  open: boolean
  onClose: () => void
  activeGate: Gate | null
  messages: ArtifactMessage[]
  isSending: boolean
  onSend: (message: string) => Promise<void>
}

const GATE_LABELS: Record<Gate, string> = {
  investigation_planner: 'Investigation Plan Review',
  finding_reviewer:      'Risk Findings Review',
}

export function ChatOverlay({
  open,
  onClose,
  activeGate,
  messages,
  isSending,
  onSend,
}: ChatOverlayProps) {
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Close on Escape when dismissible
  useEffect(() => {
    if (!open || activeGate) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, activeGate, onClose])

  if (!open) return null

  const isDismissible = !activeGate
  const headerLabel = activeGate ? GATE_LABELS[activeGate] : 'Conversation History'
  const showQuickActions = activeGate === 'investigation_planner'

  async function handleSend(text: string) {
    const trimmed = text.trim()
    if (!trimmed || isSending) return
    setInput('')
    await onSend(trimmed)
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={isDismissible ? onClose : undefined}
        aria-hidden="true"
      />

      {/* Panel */}
      <div className="relative z-10 flex h-[70vh] w-full max-w-xl flex-col rounded-xl border border-[--color-border] bg-[--color-surface] shadow-2xl">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-[--color-border] px-5 py-4">
          <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
            {headerLabel}
          </span>
          {isDismissible && (
            <button
              type="button"
              onClick={onClose}
              className="font-mono text-xs text-[--color-muted] transition-colors hover:text-[--color-text]"
              aria-label="Close"
            >
              ✕
            </button>
          )}
        </div>

        {/* Messages */}
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {messages.length === 0 && (
            <p className="py-8 text-center font-mono text-xs text-[--color-muted]">
              Waiting for agent…
            </p>
          )}
          {messages.map((msg, i) => {
            if (msg.role === 'human') {
              return (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[80%] rounded-lg border border-[--color-border] bg-[--color-surface-raised] px-4 py-3">
                    <p className="font-mono text-xs text-[--color-text]">{msg.content}</p>
                  </div>
                </div>
              )
            }
            const html = marked.parse(msg.content) as string
            return (
              <div key={i} className="rounded-lg border border-[--color-border] bg-[--color-surface-raised] px-4 py-3">
                <div
                  className={cn(
                    'prose prose-invert prose-sm max-w-none font-mono text-xs leading-relaxed',
                    'text-[--color-text] [&_strong]:text-[--color-text] [&_li]:text-[--color-muted]',
                  )}
                  dangerouslySetInnerHTML={{ __html: html }}
                />
              </div>
            )
          })}
          {isSending && (
            <div className="flex items-center gap-2 font-mono text-xs text-[--color-muted]">
              <Spinner size="sm" />
              Sending…
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Quick actions — Gate 1 only */}
        {showQuickActions && (
          <div className="shrink-0 flex flex-wrap items-center gap-2 border-t border-[--color-border] px-5 py-3">
            <span className="font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
              quick:
            </span>
            <button
              type="button"
              disabled={isSending}
              onClick={() => void handleSend('Yes, proceed with the plan')}
              className="rounded border border-[--badge-done-border] px-2.5 py-1 font-mono text-[10px] text-[--badge-done-text] transition-colors hover:bg-[--badge-done-bg] disabled:opacity-40"
            >
              Yes, proceed
            </button>
            <button
              type="button"
              disabled={isSending}
              onClick={() => void handleSend('Cancel this analysis')}
              className="rounded border border-[--badge-failed-border] px-2.5 py-1 font-mono text-[10px] text-[--badge-failed-text] transition-colors hover:bg-[--badge-failed-bg] disabled:opacity-40"
            >
              Cancel analysis
            </button>
          </div>
        )}

        {/* Input */}
        <div className="shrink-0 border-t border-[--color-border] p-4">
          <div className="flex gap-2">
            <textarea
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isSending}
              placeholder={
                activeGate === 'finding_reviewer'
                  ? 'Acknowledge findings to continue…'
                  : 'Type your response…'
              }
              className={cn(
                'flex-1 resize-none rounded border border-[--color-border] bg-[--color-surface-raised]',
                'px-3 py-2 font-mono text-xs text-[--color-text] placeholder:text-[--color-muted]/40',
                'transition-colors focus:border-[--color-accent] focus:outline-none',
                'disabled:cursor-not-allowed disabled:opacity-40',
              )}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void handleSend(input)
                }
              }}
            />
            <Button
              variant="secondary"
              size="sm"
              disabled={!input.trim() || isSending}
              onClick={() => void handleSend(input)}
            >
              Send
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
