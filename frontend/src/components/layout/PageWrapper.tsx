import type { ReactNode } from 'react'
import { cn } from '../../lib/utils'

interface PageWrapperProps {
  children: ReactNode
  className?: string
}

export function PageWrapper({ children, className }: PageWrapperProps) {
  return (
    <div className={cn('min-h-screen bg-[--color-bg] text-[--color-text]', className)}>
      <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">{children}</div>
    </div>
  )
}
