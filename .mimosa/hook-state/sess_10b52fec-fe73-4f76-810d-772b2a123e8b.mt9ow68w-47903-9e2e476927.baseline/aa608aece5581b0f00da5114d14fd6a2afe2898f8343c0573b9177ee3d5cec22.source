'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { X, Lightbulb } from 'lucide-react'
import { useAdvisor } from '@/hooks/useAdvisor'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'

export function AdvisorCard() {
  const { suggestion, dismiss } = useAdvisor()
  const [visible, setVisible] = useState(false)
  const [prevId, setPrevId] = useState<string | null>(null)

  // Animate in when suggestion changes
  useEffect(() => {
    if (suggestion && suggestion.id !== prevId) {
      setVisible(false)
      const t = setTimeout(() => {
        setVisible(true)
        setPrevId(suggestion.id)
      }, 60)
      return () => clearTimeout(t)
    }
    if (!suggestion) {
      setVisible(false)
    }
  }, [suggestion, prevId])

  if (!suggestion) return null

  return (
    <div
      className={cn(
        'fixed bottom-6 right-6 z-[9980] w-72 bg-popover border border-border rounded-xl shadow-2xl overflow-hidden',
        'transition-all duration-300',
        visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4 pointer-events-none'
      )}
      role="status"
      aria-live="polite"
      aria-label="Smart suggestion"
    >
      {/* Accent strip */}
      <div className="h-0.5 bg-gradient-to-r from-primary via-primary/60 to-transparent" />

      <div className="p-4">
        {/* Header row */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-lg flex-shrink-0" aria-hidden="true">{suggestion.icon}</span>
            <p className="font-semibold text-sm leading-tight">{suggestion.title}</p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-muted-foreground hover:text-foreground flex-shrink-0 -mt-0.5 -mr-1"
            onClick={dismiss}
            aria-label="Dismiss suggestion"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>

        {/* Body */}
        <p className="mt-1.5 text-sm text-muted-foreground leading-relaxed">
          {suggestion.body}
        </p>

        {/* Action */}
        {suggestion.action && (
          <div className="mt-3">
            <Button
              size="sm"
              className="h-7 px-3 text-xs w-full"
              asChild
              onClick={dismiss}
            >
              <Link href={suggestion.action.href}>
                {suggestion.action.label}
              </Link>
            </Button>
          </div>
        )}

        {/* Footer label */}
        <div className="mt-2.5 flex items-center gap-1 text-[10px] text-muted-foreground/60">
          <Lightbulb className="h-2.5 w-2.5" />
          Smart suggestion
        </div>
      </div>
    </div>
  )
}
