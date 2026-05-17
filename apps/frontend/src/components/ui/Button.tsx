import { cn } from '../../lib/utils'
import type { ButtonHTMLAttributes } from 'react'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost'
  size?: 'sm' | 'md' | 'lg'
}

const variantMap = {
  primary:
    'bg-[--color-accent] text-[--color-bg] hover:bg-[--color-accent-hover] border-transparent font-semibold shadow-[0_0_16px_var(--color-accent-glow)]',
  secondary:
    'bg-transparent text-[--color-accent] border-[--color-accent] hover:bg-[--color-accent]/10',
  ghost:
    'bg-transparent text-[--color-muted] border-transparent hover:text-[--color-text] hover:bg-[--color-surface-raised]',
}

const sizeMap = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-5 py-2.5 text-sm',
  lg: 'px-7 py-3 text-base',
}

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  disabled,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex cursor-pointer items-center justify-center gap-2 rounded border font-mono tracking-wide transition-all duration-150',
        'disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[--color-accent]',
        variantMap[variant],
        sizeMap[size],
        className,
      )}
      disabled={disabled}
      {...props}
    >
      {children}
    </button>
  )
}
