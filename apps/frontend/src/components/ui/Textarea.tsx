import { cn } from '../../lib/utils'
import type { TextareaHTMLAttributes } from 'react'

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string
  hint?: string
  error?: string
}

export function Textarea({ label, hint, error, className, id, ...props }: TextareaProps) {
  const textareaId = id ?? label.toLowerCase().replace(/\s+/g, '-')

  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={textareaId}
        className="font-mono text-xs font-medium tracking-widest text-(--color-muted) uppercase"
      >
        {label}
      </label>
      <textarea
        id={textareaId}
        className={cn(
          'w-full rounded border border-(--color-border) bg-(--color-surface-raised)',
          'px-3 py-2 font-mono text-xs text-(--color-text) placeholder:text-(--color-muted)/50',
          'min-h-[140px] resize-y transition-colors duration-150',
          'focus:border-(--color-accent) focus:ring-1 focus:ring-(--color-accent)/40 focus:outline-none',
          'scrollbar-thin scrollbar-thumb-(--color-border) scrollbar-track-transparent',
          error &&
            'border-(--color-error) focus:border-(--color-error) focus:ring-(--color-error)/40',
          className,
        )}
        {...props}
      />
      {hint && !error && <p className="font-mono text-xs text-(--color-muted)/70">{hint}</p>}
      {error && <p className="font-mono text-xs text-(--color-error)">{error}</p>}
    </div>
  )
}
