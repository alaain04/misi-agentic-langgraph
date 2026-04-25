import { cn } from '../../lib/utils'
import type { InputHTMLAttributes } from 'react'

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  hint?: string
  error?: string
}

export function Input({ label, hint, error, className, id, ...props }: InputProps) {
  const inputId = id ?? label.toLowerCase().replace(/\s+/g, '-')

  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={inputId}
        className="font-mono text-xs font-medium tracking-widest text-[--color-muted] uppercase"
      >
        {label}
      </label>
      <input
        id={inputId}
        className={cn(
          'w-full rounded border border-[--color-border] bg-[--color-surface-raised]',
          'px-3 py-2 font-mono text-sm text-[--color-text] placeholder:text-[--color-muted]/50',
          'transition-colors duration-150',
          'focus:border-[--color-accent] focus:ring-1 focus:ring-[--color-accent]/40 focus:outline-none',
          error &&
            'border-[--color-error] focus:border-[--color-error] focus:ring-[--color-error]/40',
          className,
        )}
        {...props}
      />
      {hint && !error && <p className="font-mono text-xs text-[--color-muted]/70">{hint}</p>}
      {error && <p className="font-mono text-xs text-[--color-error]">{error}</p>}
    </div>
  )
}
