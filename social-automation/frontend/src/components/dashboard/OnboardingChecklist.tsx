'use client'

import { useState, useEffect } from 'react'
import { CheckCircle2, Circle, X, Sparkles } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'
import Link from 'next/link'

interface ChecklistItem {
  label: string
  href: string
  completed: boolean
}

interface OnboardingChecklistProps {
  /** Number of connected social accounts */
  connectedAccounts?: number
  /** Whether a brand has been set up */
  hasBrand?: boolean
  /** Total number of posts (draft + published + scheduled) */
  postCount?: number
  /** Whether at least one post is scheduled */
  hasScheduledPost?: boolean
}

const STORAGE_KEY = 'onboarding-checklist-dismissed'

export function OnboardingChecklist({
  connectedAccounts = 0,
  hasBrand = false,
  postCount = 0,
  hasScheduledPost = false,
}: OnboardingChecklistProps) {
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    if (typeof window !== 'undefined') {
      setDismissed(localStorage.getItem(STORAGE_KEY) === 'true')
    }
  }, [])

  const handleDismiss = () => {
    setDismissed(true)
    if (typeof window !== 'undefined') {
      localStorage.setItem(STORAGE_KEY, 'true')
    }
  }

  const items: ChecklistItem[] = [
    {
      label: 'Connect a channel',
      href: '/accounts',
      completed: connectedAccounts > 0,
    },
    {
      label: 'Add brand basics',
      href: '/brand/onboarding',
      completed: hasBrand,
    },
    {
      label: 'Create your first post',
      href: '/content/new',
      completed: postCount > 0,
    },
    {
      label: 'Schedule a post',
      href: '/calendar',
      completed: hasScheduledPost,
    },
  ]

  const completedCount = items.filter(i => i.completed).length
  const progress = Math.round((completedCount / items.length) * 100)
  const allDone = completedCount === items.length

  if (dismissed || allDone) return null

  return (
    <Card className="border-primary/20">
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" />
          <CardTitle className="text-base">Getting Started</CardTitle>
        </div>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={handleDismiss}>
          <X className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Progress bar */}
        <div>
          <div className="flex items-center justify-between text-xs text-muted-foreground mb-1.5">
            <span>{completedCount} of {items.length} complete</span>
            <span>{progress}%</span>
          </div>
          <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full bg-primary transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>

        {/* Checklist items */}
        <ul className="space-y-1">
          {items.map(item => (
            <li key={item.label}>
              <Link
                href={item.href}
                className={cn(
                  'flex items-center gap-3 rounded-lg px-3 py-2 transition-colors',
                  item.completed ? 'opacity-60' : 'hover:bg-accent'
                )}
              >
                {item.completed ? (
                  <CheckCircle2 className="h-5 w-5 text-green-500 flex-shrink-0" />
                ) : (
                  <Circle className="h-5 w-5 text-muted-foreground flex-shrink-0" />
                )}
                <span className={cn('text-sm', item.completed && 'line-through')}>
                  {item.label}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
