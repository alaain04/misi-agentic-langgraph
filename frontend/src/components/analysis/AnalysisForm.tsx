import { useState } from 'react'
import { Button } from '../ui/Button'
import { Input } from '../ui/Input'
import { Textarea } from '../ui/Textarea'
import { Select } from '../ui/Select'
import { Spinner } from '../ui/Spinner'
import type { AnalysisRequest, LockFileName } from '../../api/types'

interface AnalysisFormProps {
  onSubmit: (req: AnalysisRequest) => Promise<void>
  isLoading: boolean
}

interface FormErrors {
  package_json?: string
  lock_file?: string
  concern?: string
}

const lockFileOptions = [
  { value: 'package-lock.json', label: 'package-lock.json' },
  { value: 'yarn.lock', label: 'yarn.lock' },
  { value: 'pnpm-lock.yaml', label: 'pnpm-lock.yaml' },
]

export function AnalysisForm({ onSubmit, isLoading }: AnalysisFormProps) {
  const [packageJson, setPackageJson] = useState('')
  const [lockFile, setLockFile] = useState('')
  const [lockFileName, setLockFileName] = useState<LockFileName>('package-lock.json')
  const [concern, setConcern] = useState('')
  const [errors, setErrors] = useState<FormErrors>({})

  function validate(): boolean {
    const next: FormErrors = {}
    if (!packageJson.trim()) next.package_json = 'package.json contents are required'
    if (!lockFile.trim()) next.lock_file = 'Lock file contents are required'
    if (!concern.trim()) next.concern = 'Describe the risk you want to analyze'
    setErrors(next)
    return Object.keys(next).length === 0
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!validate()) return
    await onSubmit({
      package_json: packageJson.trim(),
      lock_file: lockFile.trim(),
      lock_file_name: lockFileName,
      concern: concern.trim(),
    })
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} noValidate>
      <div className="space-y-6 rounded-lg border border-[--color-border] bg-[--color-surface] p-6">
        {/* Section label */}
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
            01 / input
          </span>
          <div className="h-px flex-1 bg-[--color-border]" />
        </div>

        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <Textarea
              label="package.json"
              id="package-json"
              value={packageJson}
              onChange={(e) => setPackageJson(e.target.value)}
              placeholder='{ "name": "my-app", "dependencies": { ... } }'
              rows={8}
              error={errors.package_json}
              disabled={isLoading}
            />
          </div>

          <div className="sm:col-span-1">
            <Select
              label="Lock file type"
              id="lock-file-name"
              value={lockFileName}
              onChange={(e) => setLockFileName(e.target.value as LockFileName)}
              options={lockFileOptions}
              disabled={isLoading}
            />
          </div>

          <div className="sm:col-span-2">
            <Textarea
              label="Lock file contents"
              id="lock-file"
              value={lockFile}
              onChange={(e) => setLockFile(e.target.value)}
              placeholder="Paste the full contents of your lock file..."
              rows={8}
              error={errors.lock_file}
              disabled={isLoading}
            />
          </div>

          <div className="sm:col-span-2">
            <Input
              label="Risk concern"
              id="concern"
              value={concern}
              onChange={(e) => setConcern(e.target.value)}
              placeholder='e.g. "supply chain attacks via transitive dependencies"'
              error={errors.concern}
              disabled={isLoading}
            />
          </div>
        </div>

        <div className="flex items-center justify-end pt-2">
          <Button type="submit" disabled={isLoading} size="md">
            {isLoading ? (
              <>
                <Spinner size="sm" />
                Analyzing…
              </>
            ) : (
              <>
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                  <path
                    d="M1 7h12M8 2l5 5-5 5"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
                Run analysis
              </>
            )}
          </Button>
        </div>
      </div>
    </form>
  )
}
