import { type ElementType } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import Link from 'next/link'

interface EmptyStateAction {
  label: string
  href?: string
  onClick?: () => void
  variant?: 'default' | 'outline' | 'ghost'
  icon?: ElementType
}

interface EmptyStateProps {
  icon: ElementType
  title: string
  description: string
  primaryAction?: EmptyStateAction
  secondaryAction?: EmptyStateAction
  className?: string
  iconClassName?: string
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  primaryAction,
  secondaryAction,
  className,
  iconClassName,
}: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center py-16 px-4 text-center', className)}>
      <div className={cn('mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-muted', iconClassName)}>
        <Icon className="h-8 w-8 text-muted-foreground" />
      </div>
      <h3 className="text-base font-semibold">{title}</h3>
      <p className="mt-1 max-w-xs text-sm text-muted-foreground leading-relaxed">{description}</p>
      {(primaryAction || secondaryAction) && (
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          {primaryAction && (
            primaryAction.href ? (
              <Button asChild variant={primaryAction.variant ?? 'default'} size="sm">
                <Link href={primaryAction.href}>
                  {primaryAction.icon && <primaryAction.icon className="mr-2 h-4 w-4" />}
                  {primaryAction.label}
                </Link>
              </Button>
            ) : (
              <Button onClick={primaryAction.onClick} variant={primaryAction.variant ?? 'default'} size="sm">
                {primaryAction.icon && <primaryAction.icon className="mr-2 h-4 w-4" />}
                {primaryAction.label}
              </Button>
            )
          )}
          {secondaryAction && (
            secondaryAction.href ? (
              <Button asChild variant={secondaryAction.variant ?? 'outline'} size="sm">
                <Link href={secondaryAction.href}>
                  {secondaryAction.icon && <secondaryAction.icon className="mr-2 h-4 w-4" />}
                  {secondaryAction.label}
                </Link>
              </Button>
            ) : (
              <Button onClick={secondaryAction.onClick} variant={secondaryAction.variant ?? 'outline'} size="sm">
                {secondaryAction.icon && <secondaryAction.icon className="mr-2 h-4 w-4" />}
                {secondaryAction.label}
              </Button>
            )
          )}
        </div>
      )}
    </div>
  )
}
