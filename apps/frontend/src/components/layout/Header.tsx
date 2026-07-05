import { useState } from 'react'
import { NavLink } from 'react-router-dom'

const NAV_LINKS = [
  { to: '/new', label: 'New analysis', end: false },
  { to: '/jobs', label: 'Executions', end: false },
]

function navClass(isActive: boolean) {
  return [
    'rounded px-3 py-1.5 font-mono text-xs tracking-widest uppercase transition-colors duration-150',
    isActive
      ? 'bg-[--color-accent]/8 text-[--color-accent]'
      : 'text-[--color-muted] hover:bg-[--color-surface-raised] hover:text-[--color-text]',
  ].join(' ')
}

export function Header() {
  const [open, setOpen] = useState(false)

  return (
    <header className="mb-10 border-b border-[--color-border]">
      <div className="flex items-center justify-between gap-4 py-6">
        {/* Logo */}
        <NavLink
          to="/"
          className="flex items-baseline gap-4 no-underline"
          onClick={() => setOpen(false)}
        >
          <span className="hidden h-8 w-1 shrink-0 rounded-full bg-[--color-accent] sm:block" />
          <div>
            <h1 className="font-display text-2xl leading-none font-bold tracking-tight text-[--color-text]">
              dep<span className="text-[--color-accent]">risk</span>
            </h1>
            <p className="mt-1 font-mono text-xs tracking-widest text-[--color-muted] uppercase">
              dependency risk intelligence
            </p>
          </div>
        </NavLink>

        {/* Desktop nav */}
        <nav className="hidden items-center gap-1 sm:flex">
          {NAV_LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) => navClass(isActive)}
            >
              {link.label}
            </NavLink>
          ))}
        </nav>

        {/* Hamburger button — mobile only */}
        <button
          className="flex h-9 w-9 flex-col items-center justify-center gap-1.5 rounded border border-[--color-border] bg-[--color-surface] transition-colors duration-150 hover:bg-[--color-surface-raised] sm:hidden"
          aria-label="Toggle navigation"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span
            className={[
              'block h-px w-4 bg-[--color-muted] transition-all duration-200',
              open ? 'translate-y-[7px] rotate-45' : '',
            ].join(' ')}
          />
          <span
            className={[
              'block h-px w-4 bg-[--color-muted] transition-all duration-200',
              open ? 'opacity-0' : '',
            ].join(' ')}
          />
          <span
            className={[
              'block h-px w-4 bg-[--color-muted] transition-all duration-200',
              open ? '-translate-y-[7px] -rotate-45' : '',
            ].join(' ')}
          />
        </button>
      </div>

      {/* Mobile dropdown */}
      <div
        className={[
          'overflow-hidden transition-all duration-200 sm:hidden',
          open ? 'max-h-40 pb-4' : 'max-h-0',
        ].join(' ')}
      >
        <nav className="flex flex-col gap-1">
          {NAV_LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) => navClass(isActive)}
              onClick={() => setOpen(false)}
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  )
}
