import type { PanelProps } from './types'
import type { ToolRunnerArtifact } from '../../../api/types'

export function ToolRunnerPanel({ artifacts }: PanelProps) {
  const artifact = artifacts.find((a) => a.node === 'tool_runner') as ToolRunnerArtifact | undefined

  if (!artifact?.iterations.length) {
    return <p className="font-mono text-xs text-(--color-muted)">No data yet.</p>
  }

  return (
    <div className="space-y-4">
      {artifact.iterations.map((iter) => (
        <div key={iter.conductor_iteration} className="space-y-2">
          <p className="font-mono text-[10px] tracking-widest text-(--color-muted) uppercase">
            Iteration {iter.conductor_iteration}
          </p>
          {iter.tools_run.length > 0 && (
            <ul className="space-y-1">
              {iter.tools_run.map((t) => {
                const failed = iter.errors.some((e) => e.tool === t)
                return (
                  <li key={t} className={`font-mono text-xs ${failed ? 'text-(--color-error)' : 'text-(--color-text)'}`}>
                    {failed ? 'x' : 'ok'} {t}
                  </li>
                )
              })}
            </ul>
          )}
          {iter.errors.length > 0 && (
            <ul className="space-y-1">
              {iter.errors.map((e, i) => (
                <li key={i} className="rounded border border-(--color-error)/30 bg-(--color-error)/5 p-2">
                  <span className="font-mono text-xs font-semibold text-(--color-error)">{e.tool}</span>
                  <p className="font-mono text-xs text-(--color-muted) mt-0.5">{e.error}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  )
}
