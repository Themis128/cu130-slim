'use client'

import { useState, useEffect } from 'react'
import { CheckCircle2, Circle, X, Sparkles } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'
import Link from 'next/link'
import { useAccounts, useBrand, useOverviewMetrics, useScheduledPosts } from '@/hooks/useQueries'

interface ChecklistItem {
  label: string
  href: string
  completed: boolean
}

interface OnboardingChecklistProps {
  /** Number of connected social accounts (optional override) */
  connectedAccounts?: number
  /** Whether a brand has been set up (optional override) */
  hasBrand?: boolean
  /** Total number of posts (optional override) */
  postCount?: number
  /** Whether at least one post is scheduled (optional override) */
  hasScheduledPost?: boolean
}

const STORAGE_KEY = 'onboarding-checklist-dismissed'

export function OnboardingChecklist({
  connectedAccounts,
  hasBrand,
  postCount,
  hasScheduledPost,
}: OnboardingChecklistProps) {
  const [dismissed, setDismissed] = useState(false)
  const { data: accounts } = useAccounts()
  const { data: brand } = useBrand()
  const { data: scheduledPosts = [] } = useScheduledPosts()
  const { data: metrics } = useOverviewMetrics(30)

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

  const resolvedAccounts = Math.max(
    connectedAccounts ?? 0,
    accounts?.length ?? 0,
    metrics?.connected_accounts ?? 0,
  )
  const resolvedHasBrand = !!brand || hasBrand === true
  const resolvedPostCount = Math.max(postCount ?? 0, metrics?.total_posts ?? 0)
  const resolvedHasScheduled = (
    (scheduledPosts?.length ?? 0) > 0
    || (metrics?.scheduled_posts ?? 0) > 0
    || hasScheduledPost === true
  )

  const items: ChecklistItem[] = [
    {
      label: 'Connect a channel',
      href: '/accounts',
      completed: resolvedAccounts > 0,
    },
    {
      label: 'Add brand basics',
      href: '/brand/onboarding',
      completed: resolvedHasBrand,
    },
    {
      label: 'Create your first post',
      href: '/content/new',
      completed: resolvedPostCount > 0,
    },
    {
      label: 'Schedule a post',
      href: '/calendar',
      completed: resolvedHasScheduled,
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
