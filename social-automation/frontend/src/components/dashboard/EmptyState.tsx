import { type ElementType } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import Link from 'next/link'

interface EmptyStateProps {
  icon: ElementType
  title: string
  description: string
  actionLabel?: string
  actionHref?: string
  className?: string
}

/**
 * Reusable dashboard empty-state card.
 * Shows a centered icon, message, and optional CTA button inside a Card.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  actionLabel,
  actionHref,
  className,
}: EmptyStateProps) {
  return (
    <Card className={cn('border-dashed', className)}>
      <CardContent className="flex flex-col items-center justify-center py-10 px-6 text-center">
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
          <Icon className="h-6 w-6 text-muted-foreground" />
        </div>
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="mt-1 max-w-sm text-sm text-muted-foreground leading-relaxed">
          {description}
        </p>
        {actionLabel && actionHref && (
          <Button asChild size="sm" className="mt-4">
            <Link href={actionHref}>
              {actionLabel}
            </Link>
          </Button>
        )}
      </CardContent>
    </Card>
  )
}
