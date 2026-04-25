import { NavLink } from 'react-router-dom'

export function Header() {
  return (
    <header className="mb-10 border-b border-[--color-border] py-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-baseline gap-4">
          {/* Accent bar */}
          <span className="hidden h-8 w-1 shrink-0 rounded-full bg-[--color-accent] sm:block" />
          <div>
            <h1 className="font-display text-2xl leading-none font-bold tracking-tight text-[--color-text]">
              dep<span className="text-[--color-accent]">risk</span>
            </h1>
            <p className="mt-1 font-mono text-xs tracking-widest text-[--color-muted] uppercase">
              dependency vulnerability scanner
            </p>
          </div>
        </div>

        <nav className="flex items-center gap-1">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              [
                'rounded px-3 py-1.5 font-mono text-xs tracking-widest uppercase transition-colors duration-150',
                isActive
                  ? 'bg-[--color-accent]/8 text-[--color-accent]'
                  : 'text-[--color-muted] hover:bg-[--color-surface-raised] hover:text-[--color-text]',
              ].join(' ')
            }
          >
            Scan
          </NavLink>
          <NavLink
            to="/jobs"
            className={({ isActive }) =>
              [
                'rounded px-3 py-1.5 font-mono text-xs tracking-widest uppercase transition-colors duration-150',
                isActive
                  ? 'bg-[--color-accent]/8 text-[--color-accent]'
                  : 'text-[--color-muted] hover:bg-[--color-surface-raised] hover:text-[--color-text]',
              ].join(' ')
            }
          >
            Executions
          </NavLink>
        </nav>
      </div>
    </header>
  )
}
