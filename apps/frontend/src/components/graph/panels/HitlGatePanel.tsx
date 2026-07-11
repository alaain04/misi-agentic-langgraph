import type { PanelProps } from './types'
import type { HitlGateArtifact } from '../../../api/types'

export function HitlGatePanel({ artifacts }: PanelProps) {
  const artifact = artifacts.find((a) => a.node === 'hitl_gate') as HitlGateArtifact | undefined
  const messages = artifact?.messages ?? []

  if (messages.length === 0) {
    return <p className="font-mono text-xs text-(--color-muted)">No messages yet.</p>
  }

  return (
    <div className="space-y-2">
      {messages.map((msg, i) => (
        <div
          key={i}
          className={`rounded border p-3 font-mono text-xs leading-relaxed ${
            msg.role === 'human'
              ? 'border-(--color-border) bg-(--color-surface-raised) text-(--color-text)'
              : 'border-(--color-accent)/30 bg-(--color-accent)/5 text-(--color-text)'
          }`}
        >
          <p className="mb-1 text-[10px] tracking-widest text-(--color-muted) uppercase">
            {msg.role === 'human' ? 'User' : 'Agent'}
            {msg.type && ` · ${msg.type}`}
          </p>
          <p>{msg.content}</p>
        </div>
      ))}
    </div>
  )
}
