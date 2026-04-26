import { useState } from 'react'
import { SAMPLES } from '../data/samples'
import { ScanModal } from '../components/analysis/ScanModal'
import type { JobMetadata } from '../api/types'

const SAMPLE_ICONS: Record<string, string> = {
  'supply-chain': '⛓',
  'license-risk': '⚖',
  'outdated-cves': '🛡',
  typosquatting: '🎭',
}

export default function ScanPage() {
  const [modalOpen, setModalOpen] = useState(false)
  const [modalInitial, setModalInitial] = useState<Partial<JobMetadata> | undefined>()
  const [modalTitle, setModalTitle] = useState<string | undefined>()

  function openBlank() {
    setModalInitial(undefined)
    setModalTitle(undefined)
    setModalOpen(true)
  }

  return (
    <main className="space-y-8">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs font-semibold tracking-widest text-[--color-accent] uppercase">
          new scan
        </span>
        <div className="h-px flex-1 bg-[--color-border]" />
      </div>

      {/* New scan card */}
      <div>
        <p className="mb-4 font-mono text-xs text-[--color-muted]">Start from scratch</p>
        <button
          onClick={openBlank}
          className="group w-full rounded-xl border border-dashed border-[--color-border] bg-[--color-surface] p-8 text-left transition-all duration-200 hover:border-[--color-accent]/50 hover:bg-[--color-surface-raised]"
        >
          <div className="flex items-center gap-5">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border border-[--color-border] bg-[--color-surface-raised] text-2xl transition-colors duration-200 group-hover:border-[--color-accent]/40 group-hover:bg-[--color-accent]/5">
              +
            </div>
            <div>
              <div className="font-display mb-1 text-lg font-bold text-[--color-text]">
                New scan
              </div>
              <div className="font-mono text-xs text-[--color-muted]">
                Paste or drag your package.json and lock file to begin a custom analysis
              </div>
            </div>
            <div className="ml-auto font-mono text-lg text-[--color-muted] transition-colors duration-200 group-hover:text-[--color-accent]">
              →
            </div>
          </div>
        </button>
      </div>

      {/* Divider */}
      <div className="flex items-center gap-3">
        <div className="h-px flex-1 bg-[--color-border]" />
        <span className="font-mono text-xs text-[--color-muted]">or pick a sample</span>
        <div className="h-px flex-1 bg-[--color-border]" />
      </div>

      {/* Sample cards */}
      <div>
        <p className="mb-4 font-mono text-xs text-[--color-muted]">
          Curated examples — ready to run
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          {SAMPLES.map((sample) => (
            <button
              key={sample.id}
              onClick={() => {
                setModalInitial(sample.request.metadata)
                setModalTitle(sample.label)
                setModalOpen(true)
              }}
              className="group rounded-xl border border-[--color-border] bg-[--color-surface] p-6 text-left transition-all duration-200 hover:border-[--color-accent]/40 hover:bg-[--color-surface-raised]"
            >
              <div className="mb-3 flex items-start justify-between gap-2">
                <span className="text-2xl">{SAMPLE_ICONS[sample.id] ?? '📦'}</span>
                <span className="font-mono text-xs text-[--color-muted] transition-colors duration-200 group-hover:text-[--color-accent]">
                  use →
                </span>
              </div>
              <div className="font-display mb-1 text-sm font-bold text-[--color-text]">
                {sample.label}
              </div>
              <div className="font-mono text-xs leading-relaxed text-[--color-muted]">
                {sample.description}
              </div>
            </button>
          ))}
        </div>
      </div>

      <ScanModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        initialRequest={modalInitial}
        title={modalTitle}
      />
    </main>
  )
}
