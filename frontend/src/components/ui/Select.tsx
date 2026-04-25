import { cn } from '../../lib/utils'
import type { SelectHTMLAttributes } from 'react'

interface SelectOption {
  value: string
  label: string
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string
  options: SelectOption[]
  hint?: string
  error?: string
}

export function Select({ label, options, hint, error, className, id, ...props }: SelectProps) {
  const selectId = id ?? label.toLowerCase().replace(/\s+/g, '-')

  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={selectId}
        className="font-mono text-xs font-medium tracking-widest text-[--color-muted] uppercase"
      >
        {label}
      </label>
      <div className="relative">
        <select
          id={selectId}
          className={cn(
            'w-full appearance-none rounded border border-[--color-border] bg-[--color-surface-raised]',
            'px-3 py-2 pr-9 font-mono text-sm text-[--color-text]',
            'cursor-pointer transition-colors duration-150',
            'focus:border-[--color-accent] focus:ring-1 focus:ring-[--color-accent]/40 focus:outline-none',
            error &&
              'border-[--color-error] focus:border-[--color-error] focus:ring-[--color-error]/40',
            className,
          )}
          {...props}
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        {/* Custom chevron */}
        <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-[--color-muted]">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path
              d="M2 4L6 8L10 4"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </div>
      {hint && !error && <p className="font-mono text-xs text-[--color-muted]/70">{hint}</p>}
      {error && <p className="font-mono text-xs text-[--color-error]">{error}</p>}
    </div>
  )
}
