import { useNavigate } from 'react-router-dom'
import { Button } from '../components/ui/Button'

const FEATURES = [
  {
    icon: '⛓',
    title: 'Supply chain threads',
    description:
      'Identify malicious install scripts, hidden network beacons, and compromised transitive dependencies before they reach production.',
  },
  {
    icon: '⚖',
    title: 'License compliance',
    description:
      'Surface restrictive licenses hidden in your dependency tree that could impact commercial use or internal policy requirements.',
  },
  {
    icon: '🛡',
    title: 'Known vulnerabilities',
    description:
      'Detect dependencies affected by published CVEs and prioritize them by exploitability, impact, and upgrade urgency.',
  },
  // {
  //   icon: '🎭',
  //   title: 'Typosquatting',
  //   description:
  //     'Detect typosquatting and dependency confusion attempts by identifying packages designed to mimic trusted libraries.',
  // },
  {
    icon: '🧭',
    title: 'Remediation guidance',
    description:
      'Receive explainable recommendations for safer versions, replacements, and migration paths for every critical finding.',
  },
]

const STEPS = [
  { n: '01', label: 'Enter repository URL', detail: 'Paste any GitHub repository URL' },
  { n: '02', label: 'AI builds the analysis plan', detail: 'Hypotheses and skill assignments generated' },
  { n: '03', label: 'Review and approve', detail: 'Inspect the plan before execution starts' },
  { n: '04', label: 'Receive actionable report', detail: 'Prioritised risks with remediation guidance' },
]

export function LandingPage() {
  const navigate = useNavigate()

  return (
    <main className="space-y-24 pb-24">
      {/* ── Hero ─────────────────────────────────────────────────────── */}
      <section className="pt-12 pb-4 text-center">
        {/* Glow blob */}
        <div
          className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-96 opacity-30"
          style={{
            background:
              'radial-gradient(ellipse 80% 60% at 50% 0%, rgba(245,166,35,0.18) 0%, transparent 70%)',
          }}
        />

        <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-(--color-border) bg-(--color-surface) px-4 py-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-(--color-accent)" />
          <span className="font-mono text-xs tracking-widest text-(--color-muted) uppercase">
            AI-powered · Javascript
          </span>
        </div>

        <h2
          className="font-display mx-auto mb-5 max-w-2xl text-5xl leading-[1.1] font-bold tracking-tight text-(--color-text)"
          style={{ textShadow: '0 0 60px rgba(245,166,35,0.12)' }}
        >
          Scan your dependencies.
          <br />
          <span className="text-(--color-accent)">Resolve the risk.</span>
        </h2>

        <p className="mx-auto mb-10 max-w-lg font-mono text-sm leading-relaxed text-(--color-muted)">
          AI-driven dependency intelligence for Javascript-based projects. Detect security,
          maintenance, and compliance issues — then receive explainable remediation guidance with a
          full audit trail.
        </p>

        <div className="flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <Button size="lg" onClick={() => navigate('/new')}>
            Start scanning →
          </Button>
        </div>
      </section>

      {/* ── Feature grid ─────────────────────────────────────────────── */}
      <section>
        <div className="mb-8 flex items-center gap-3">
          <span className="font-mono text-xs font-semibold tracking-widest text-(--color-accent) uppercase">
            how deprisk helps
          </span>
          <div className="h-px flex-1 bg-(--color-border)" />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className="group rounded-xl border border-(--color-border) bg-(--color-surface) p-6 transition-colors duration-200 hover:border-(--color-accent)/30 hover:bg-(--color-surface-raised)"
            >
              <div className="mb-2 flex items-center gap-2">
                <span className="text-2xl">{f.icon}</span>
                <h3 className="font-display text-base font-bold text-(--color-text)">{f.title}</h3>
              </div>
              <p className="font-mono text-xs leading-relaxed text-(--color-muted)">
                {f.description}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* ── How it works ─────────────────────────────────────────────── */}
      <section>
        <div className="mb-8 flex items-center gap-3">
          <span className="font-mono text-xs font-semibold tracking-widest text-(--color-accent) uppercase">
            how it works
          </span>
          <div className="h-px flex-1 bg-(--color-border)" />
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s, i) => (
            <div key={s.n} className="relative h-full">
              {i < STEPS.length - 1 && (
                <div className="absolute top-6 left-full z-10 hidden h-px w-4 bg-(--color-border) lg:block" />
              )}
              <div className="flex h-full flex-col rounded-xl border border-(--color-border) bg-(--color-surface) p-5">
                <div className="font-display mb-3 text-3xl font-bold text-(--color-accent)/30">
                  {s.n}
                </div>
                <div className="font-mono text-sm font-semibold text-(--color-text)">{s.label}</div>
                <div className="mt-1 font-mono text-xs text-(--color-muted)">{s.detail}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── CTA banner ───────────────────────────────────────────────── */}
      <section>
        <div
          className="relative overflow-hidden rounded-2xl border border-(--color-accent)/20 p-10 text-center"
          style={{
            background:
              'linear-gradient(135deg, rgba(245,166,35,0.06) 0%, rgba(245,166,35,0.02) 100%)',
          }}
        >
          <div
            className="pointer-events-none absolute inset-0 -z-10"
            style={{
              background:
                'radial-gradient(ellipse 60% 80% at 50% 100%, rgba(245,166,35,0.08) 0%, transparent 70%)',
            }}
          />
          <h3 className="font-display mb-3 text-2xl font-bold text-(--color-text)">
            Ready to audit your project?
          </h3>
          <p className="mx-auto mb-6 max-w-sm font-mono text-xs text-(--color-muted)">
            Start with one of our curated samples or paste your own package.json and lock file.
          </p>
          <Button size="lg" onClick={() => navigate('/new')}>
            Launch scanner →
          </Button>
        </div>
      </section>
    </main>
  )
}
