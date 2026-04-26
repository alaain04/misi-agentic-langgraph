import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Modal } from '../ui/Modal'
import { Button } from '../ui/Button'
import { Textarea } from '../ui/Textarea'
import { Select } from '../ui/Select'
import { Spinner } from '../ui/Spinner'
import { submitAnalysis } from '../../api/analyze'
import type { JobMetadata, LockFileName } from '../../api/types'

interface ScanModalProps {
  open: boolean
  onClose: () => void
  initialRequest?: Partial<JobMetadata>
  title?: string
}

interface FormErrors {
  concern?: string
  package_json?: string
  lock_file?: string
}

const lockFileOptions = [
  { value: 'package-lock.json', label: 'package-lock.json' },
  { value: 'yarn.lock', label: 'yarn.lock' },
  { value: 'pnpm-lock.yaml', label: 'pnpm-lock.yaml' },
]

const LOCK_FILE_NAMES: LockFileName[] = ['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml']

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => resolve(e.target?.result as string)
    reader.onerror = () => reject(new Error(`Failed to read ${file.name}`))
    reader.readAsText(file)
  })
}

function isValidPackageJson(text: string): boolean {
  try {
    const parsed = JSON.parse(text)
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
  } catch {
    return false
  }
}

export function ScanModal({ open, onClose, initialRequest, title }: ScanModalProps) {
  const navigate = useNavigate()
  const [concern, setConcern] = useState(initialRequest?.concern ?? '')
  const [packageJson, setPackageJson] = useState(initialRequest?.package_json ?? '')
  const [lockFile, setLockFile] = useState(initialRequest?.lock_file ?? '')
  const [lockFileName, setLockFileName] = useState<LockFileName>(
    (initialRequest?.lock_file_name as LockFileName) ?? 'package-lock.json',
  )
  const [errors, setErrors] = useState<FormErrors>({})
  const [isLoading, setIsLoading] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [dropError, setDropError] = useState<string | null>(null)

  useEffect(() => {
    setConcern(initialRequest?.concern ?? '')
    setPackageJson(initialRequest?.package_json ?? '')
    setLockFile(initialRequest?.lock_file ?? '')
    setLockFileName((initialRequest?.lock_file_name as LockFileName) ?? 'package-lock.json')
    setErrors({})
    setApiError(null)
    setDropError(null)
  }, [initialRequest])

  const processFiles = useCallback(async (files: File[]) => {
    let didLoad = false
    for (const file of files) {
      if (file.name === 'package.json') {
        const text = await readFileAsText(file)
        setPackageJson(text)
        setErrors((p) => ({ ...p, package_json: undefined }))
        didLoad = true
      } else if ((LOCK_FILE_NAMES as string[]).includes(file.name)) {
        const text = await readFileAsText(file)
        setLockFile(text)
        setLockFileName(file.name as LockFileName)
        setErrors((p) => ({ ...p, lock_file: undefined }))
        didLoad = true
      }
    }
    if (!didLoad) {
      setDropError(
        'No recognized file — drop a package.json or a lock file (package-lock.json, yarn.lock, pnpm-lock.yaml)',
      )
    } else {
      setDropError(null)
    }
  }, [])

  // Overlay drop handler — fires when isDragging and the transparent overlay is on top.
  // Use dataTransfer.items (not .files) and call getAsFile() synchronously before any async work,
  // because the DataTransfer object is cleared after the event handler returns.
  const handleOverlayDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      e.stopPropagation()
      setIsDragging(false)
      const files = Array.from(e.dataTransfer.items)
        .filter((item) => item.kind === 'file')
        .map((item) => item.getAsFile())
        .filter((f): f is File => f !== null)
      void processFiles(files)
    },
    [processFiles],
  )

  // Zone-level drag events (outer dashed border)
  const handleZoneDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(true)
    setDropError(null)
  }, [])

  const handleZoneDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setIsDragging(false)
    }
  }, [])

  function validate(): boolean {
    const next: FormErrors = {}
    if (!concern.trim()) next.concern = 'Describe the risk you want to analyze'
    if (!packageJson.trim()) {
      next.package_json = 'package.json contents are required'
    } else if (!isValidPackageJson(packageJson)) {
      next.package_json = 'Must be valid JSON (object)'
    }
    if (!lockFile.trim()) next.lock_file = 'Lock file contents are required'
    setErrors(next)
    return Object.keys(next).length === 0
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!validate()) return
    setIsLoading(true)
    setApiError(null)
    try {
      const res = await submitAnalysis({
        metadata: {
          concern: concern.trim(),
          package_json: packageJson.trim(),
          lock_file: lockFile.trim(),
          lock_file_name: lockFileName,
        },
      })
      navigate(`/jobs/${res.trace_id}/plan`)
    } catch (err) {
      setApiError(err instanceof Error ? err.message : String(err))
      setIsLoading(false)
    }
  }

  return (
    <Modal open={open} onClose={isLoading ? () => {} : onClose}>
      <form onSubmit={(e) => void handleSubmit(e)} noValidate>
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[--color-border] px-6 py-4">
          <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
            {title ?? 'new scan'}
          </span>
          <button
            type="button"
            onClick={onClose}
            disabled={isLoading}
            className="font-mono text-sm text-[--color-muted] transition-colors hover:text-[--color-text] disabled:opacity-40"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="space-y-6 p-6">
          {/* 1. Concern */}
          <div>
            <div className="mb-4 flex items-center gap-3">
              <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
                01 / concern
              </span>
              <div className="h-px flex-1 bg-[--color-border]" />
            </div>
            <Textarea
              autoFocus
              label="What risk do you want to analyze?"
              id="concern"
              value={concern}
              onChange={(e) => {
                setConcern(e.target.value)
                setErrors((p) => ({ ...p, concern: undefined }))
              }}
              placeholder='e.g. "supply chain attacks via transitive dependencies"'
              rows={3}
              error={errors.concern}
              disabled={isLoading}
            />
          </div>

          {/* 2. Packages — drag-and-drop zone */}
          <div>
            <div className="mb-4 flex items-center gap-3">
              <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
                02 / packages
              </span>
              <div className="h-px flex-1 bg-[--color-border]" />
            </div>

            <div
              onDragOver={handleZoneDragOver}
              onDragLeave={handleZoneDragLeave}
              className={[
                'relative space-y-5 rounded-lg border border-dashed p-4 transition-colors duration-150',
                isDragging
                  ? 'border-[--color-accent] bg-[--color-accent]/5'
                  : 'border-[--color-border]',
              ].join(' ')}
            >
              {/* Transparent overlay captures drop before textareas can intercept */}
              {isDragging && (
                <div
                  className="absolute inset-0 z-10 flex cursor-copy items-center justify-center rounded-lg"
                  onDrop={handleOverlayDrop}
                  onDragOver={(e) => e.preventDefault()}
                >
                  <span className="font-mono text-sm font-semibold text-[--color-accent]">
                    Drop files here
                  </span>
                </div>
              )}

              <p className="font-mono text-xs text-[--color-muted]">
                Paste below or drop your files anywhere in this area
              </p>

              <Textarea
                label="package.json"
                id="package-json"
                value={packageJson}
                onChange={(e) => {
                  setPackageJson(e.target.value)
                  setErrors((p) => ({ ...p, package_json: undefined }))
                }}
                placeholder='{ "name": "my-app", "dependencies": { ... } }'
                rows={6}
                error={errors.package_json}
                disabled={isLoading}
              />

              <div className="grid grid-cols-2 items-end gap-4">
                <Select
                  label="Lock file type"
                  id="lock-file-name"
                  value={lockFileName}
                  onChange={(e) => setLockFileName(e.target.value as LockFileName)}
                  options={lockFileOptions}
                  disabled={isLoading}
                />
                <div className="pb-1 font-mono text-xs text-[--color-muted]">
                  {lockFile
                    ? `${lockFile.split('\n').length} lines loaded`
                    : 'No lock file loaded yet'}
                </div>
              </div>

              <Textarea
                label="Lock file contents"
                id="lock-file"
                value={lockFile}
                onChange={(e) => {
                  setLockFile(e.target.value)
                  setErrors((p) => ({ ...p, lock_file: undefined }))
                }}
                placeholder="Paste the full contents of your lock file…"
                rows={6}
                error={errors.lock_file}
                disabled={isLoading}
              />
            </div>
          </div>

          {/* Drop error */}
          {dropError && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3">
              <p className="font-mono text-xs text-amber-400">{dropError}</p>
            </div>
          )}

          {/* API error */}
          {apiError && (
            <div className="rounded-lg border border-[--color-error]/40 bg-[--color-error]/5 px-4 py-3">
              <p className="font-mono text-xs text-[--color-error]">
                <span className="font-semibold">Error: </span>
                {apiError}
              </p>
            </div>
          )}

          {/* Submit */}
          <div className="flex items-center justify-end pt-1">
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
    </Modal>
  )
}
