// src/pages/NewAnalysisPage.tsx
import { useState } from 'react'
import { useJobSubmit } from '../hooks/useJobSubmit'
import { SAMPLES } from '../data/samples'
import { Button } from '../components/ui/Button'
import { Spinner } from '../components/ui/Spinner'
import { cn } from '../lib/utils'

const CONCERN_PILLS = [
  'Supply chain risks',
  'Known CVEs',
  'License compliance',
  'Maintainer trust',
  'Blast radius',
]

const AUTOPILOT_KEY = 'deprisk.autopilot'

function loadAutopilot(): boolean {
  return localStorage.getItem(AUTOPILOT_KEY) === 'true'
}

function saveAutopilot(value: boolean): void {
  localStorage.setItem(AUTOPILOT_KEY, String(value))
}

interface FormErrors {
  repo_url?: string
  concern?: string
}

export default function NewAnalysisPage() {
  const [repoUrl, setRepoUrl] = useState('')
  const [concern, setConcern] = useState('')
  const [autopilot, setAutopilot] = useState(loadAutopilot)
  const [errors, setErrors] = useState<FormErrors>({})
  const { submit, isSubmitting, error: submitError } = useJobSubmit()

  function validate(): boolean {
    const next: FormErrors = {}
    if (!repoUrl.trim()) next.repo_url = 'Repository URL is required'
    else if (!repoUrl.startsWith('https://')) next.repo_url = 'URL must start with https://'
    if (!concern.trim()) next.concern = 'Describe the risk you want to investigate'
    setErrors(next)
    return Object.keys(next).length === 0
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!validate()) return
    await submit({ repo_url: repoUrl.trim(), concern: concern.trim() })
  }

  function handleAutopilotChange(checked: boolean) {
    setAutopilot(checked)
    saveAutopilot(checked)
  }

  return (
    <main className="space-y-8">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
          new analysis
        </span>
        <div className="h-px flex-1 bg-[--color-border]" />
      </div>

      <div className="grid gap-10 lg:grid-cols-2">
        {/* Left: form */}
        <form onSubmit={(e) => void handleSubmit(e)} noValidate className="space-y-6">
          {/* Repo URL */}
          <div className="space-y-1.5">
            <label htmlFor="repo-url" className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
              GitHub Repository URL
            </label>
            <input
              id="repo-url"
              type="url"
              value={repoUrl}
              onChange={(e) => { setRepoUrl(e.target.value); setErrors((p) => ({ ...p, repo_url: undefined })) }}
              placeholder="https://github.com/org/repo"
              disabled={isSubmitting}
              className={cn(
                'w-full rounded border bg-[--color-surface-raised] px-3 py-2 font-mono text-sm text-[--color-text]',
                'placeholder:text-[--color-muted]/40 transition-colors focus:outline-none',
                errors.repo_url
                  ? 'border-[--color-error] focus:border-[--color-error]'
                  : 'border-[--color-border] focus:border-[--color-accent]',
                'disabled:cursor-not-allowed disabled:opacity-40',
              )}
            />
            {errors.repo_url && (
              <p className="font-mono text-[10px] text-[--color-error]">{errors.repo_url}</p>
            )}
          </div>

          {/* Concern */}
          <div className="space-y-1.5">
            <label htmlFor="concern" className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
              What risk do you want to investigate?
            </label>
            <textarea
              id="concern"
              rows={3}
              value={concern}
              onChange={(e) => { setConcern(e.target.value); setErrors((p) => ({ ...p, concern: undefined })) }}
              placeholder='e.g. "supply chain attack via malicious postinstall scripts"'
              disabled={isSubmitting}
              className={cn(
                'w-full resize-none rounded border bg-[--color-surface-raised] px-3 py-2 font-mono text-sm text-[--color-text]',
                'placeholder:text-[--color-muted]/40 transition-colors focus:outline-none',
                errors.concern
                  ? 'border-[--color-error] focus:border-[--color-error]'
                  : 'border-[--color-border] focus:border-[--color-accent]',
                'disabled:cursor-not-allowed disabled:opacity-40',
              )}
            />
            {errors.concern && (
              <p className="font-mono text-[10px] text-[--color-error]">{errors.concern}</p>
            )}
          </div>

          {/* Concern pills */}
          <div className="space-y-2">
            <p className="font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
              or pick a concern
            </p>
            <div className="flex flex-wrap gap-2">
              {CONCERN_PILLS.map((pill) => (
                <button
                  key={pill}
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => { setConcern(pill); setErrors((p) => ({ ...p, concern: undefined })) }}
                  className={cn(
                    'rounded-full border px-3 py-1 font-mono text-[10px] transition-colors',
                    concern === pill
                      ? 'border-[--color-accent] bg-[--color-accent]/10 text-[--color-accent]'
                      : 'border-[--color-border] bg-[--color-surface-raised] text-[--color-muted] hover:border-[--color-accent]/40 hover:text-[--color-text]',
                    'disabled:cursor-not-allowed disabled:opacity-40',
                  )}
                >
                  {pill}
                </button>
              ))}
            </div>
          </div>

          {/* Autopilot */}
          <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-[--color-border] bg-[--color-surface] p-4">
            <input
              type="checkbox"
              checked={autopilot}
              onChange={(e) => handleAutopilotChange(e.target.checked)}
              className="mt-0.5 accent-[--color-accent]"
            />
            <div>
              <p className="font-mono text-xs font-semibold text-[--color-text]">Autopilot mode</p>
              <p className="font-mono text-[10px] leading-relaxed text-[--color-muted]">
                The AI auto-approves both review gates and runs to completion without asking for your input.
              </p>
            </div>
          </label>

          {/* API error */}
          {submitError && (
            <div className="rounded-lg border border-[--color-error]/40 bg-[--color-error]/5 px-4 py-3">
              <p className="font-mono text-xs text-[--color-error]">{submitError.message}</p>
            </div>
          )}

          <div className="flex justify-end">
            <Button type="submit" disabled={isSubmitting} size="md">
              {isSubmitting ? (
                <>
                  <Spinner size="sm" />
                  Starting...
                </>
              ) : (
                'Run analysis ->'
              )}
            </Button>
          </div>
        </form>

        {/* Right: sample repos */}
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] tracking-widest text-[--color-muted] uppercase">
              sample repositories
            </span>
            <div className="h-px flex-1 bg-[--color-border]" />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {SAMPLES.map((sample) => (
              <button
                key={sample.id}
                type="button"
                disabled={isSubmitting}
                onClick={() => {
                  setRepoUrl(sample.repo_url)
                  setConcern(sample.concern)
                  setErrors({})
                }}
                className="group rounded-xl border border-[--color-border] bg-[--color-surface] p-5 text-left transition-all duration-200 hover:border-[--color-accent]/40 hover:bg-[--color-surface-raised] disabled:cursor-not-allowed disabled:opacity-40"
              >
                <div className="mb-2 flex items-start justify-between gap-2">
                  <span className="font-display text-sm font-bold text-[--color-text]">
                    {sample.label}
                  </span>
                  <span className="font-mono text-xs text-[--color-muted] transition-colors group-hover:text-[--color-accent]">
                    use -&gt;
                  </span>
                </div>
                <p className="font-mono text-[10px] leading-relaxed text-[--color-muted]">
                  {sample.description}
                </p>
              </button>
            ))}
          </div>
        </div>
      </div>
    </main>
  )
}
