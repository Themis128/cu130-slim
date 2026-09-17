'use client'

import { useState } from 'react'
import Link from 'next/link'
import {
  Inbox as InboxIcon,
  MessageCircle,
  Instagram,
  PhoneCall,
  RefreshCw,
  ExternalLink,
  Lock,
  Loader2,
  AlertCircle,
  User,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { useInbox } from '@/hooks/useQueries'
import { cn } from '@/lib/utils'
import { formatDistanceToNow } from 'date-fns'
import type { UnifiedConversation } from '@/types'

const PLATFORM_META: Record<string, { label: string; icon: typeof MessageCircle; badge: string }> = {
  messenger: { label: 'Page Messenger', icon: MessageCircle, badge: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300' },
  personal_messenger: { label: 'Personal Messenger', icon: User, badge: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300' },
  instagram: { label: 'Instagram', icon: Instagram, badge: 'bg-pink-100 text-pink-700 dark:bg-pink-950 dark:text-pink-300' },
  whatsapp: { label: 'WhatsApp', icon: PhoneCall, badge: 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300' },
}

function platformMeta(platform: string) {
  return PLATFORM_META[platform] ?? { label: platform, icon: MessageCircle, badge: 'bg-muted text-muted-foreground' }
}

function relativeTime(ts: string | null): string {
  if (!ts) return ''
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ''
  return formatDistanceToNow(d, { addSuffix: true })
}

export default function InboxPage() {
  const [platformFilter, setPlatformFilter] = useState<string>('all')
  const [unreadOnly, setUnreadOnly] = useState(false)
  const { data: inbox, isLoading, isError, refetch, isRefetching } = useInbox()

  const conversations = inbox?.conversations ?? []
  const byPlatform = inbox?.by_platform ?? {}
  const unreadCount = conversations.filter((c) => c.unread).length

  const filtered = conversations.filter((c) => {
    if (platformFilter !== 'all' && c.platform !== platformFilter) return false
    if (unreadOnly && !c.unread) return false
    return true
  })

  const platformKeys = Object.keys(byPlatform).sort()

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            Inbox
            {unreadCount > 0 && (
              <Badge className="bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300">
                {unreadCount} unread
              </Badge>
            )}
          </h1>
          <p className="text-muted-foreground">
            All conversations across connected platforms in one place
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isRefetching}>
          {isRefetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          <span className="ml-2">Refresh</span>
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setPlatformFilter('all')}
          className={cn(
            'rounded-full border px-3 py-1 text-sm transition-colors',
            platformFilter === 'all'
              ? 'border-primary bg-primary text-primary-foreground'
              : 'border-border hover:bg-muted'
          )}
        >
          All ({inbox?.total ?? 0})
        </button>
        {platformKeys.map((p) => {
          const meta = platformMeta(p)
          const Icon = meta.icon
          return (
            <button
              key={p}
              onClick={() => setPlatformFilter(p)}
              className={cn(
                'flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm transition-colors',
                platformFilter === p
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-border hover:bg-muted'
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {meta.label} ({byPlatform[p]})
            </button>
          )
        })}
        <button
          onClick={() => setUnreadOnly((v) => !v)}
          className={cn(
            'ml-auto rounded-full border px-3 py-1 text-sm transition-colors',
            unreadOnly ? 'border-red-400 bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300' : 'border-border hover:bg-muted'
          )}
        >
          Unread only
        </button>
      </div>

      {/* Conversation list */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : isError ? (
        <Card>
          <CardContent className="flex items-center gap-3 py-8 text-muted-foreground">
            <AlertCircle className="h-5 w-5 text-destructive" />
            Failed to load the inbox. Check that the social-api is reachable.
          </CardContent>
        </Card>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={InboxIcon}
          title={conversations.length === 0 ? 'No conversations yet' : 'Nothing matches this filter'}
          description={
            conversations.length === 0
              ? 'When people message your connected pages and accounts, their conversations will show up here.'
              : 'Try a different platform filter or clear "Unread only".'
          }
        />
      ) : (
        <div className="divide-y rounded-lg border bg-card">
          {filtered.map((c) => {
            const meta = platformMeta(c.platform)
            const Icon = meta.icon
            const key = `${c.platform}:${c.account_id}:${c.thread_id ?? c.sender_name}`
            return (
              <div
                key={key}
                className={cn(
                  'flex items-center gap-4 px-4 py-3 transition-colors hover:bg-muted/50',
                  c.unread && 'bg-blue-50/50 dark:bg-blue-950/20'
                )}
              >
                <span className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-full', meta.badge)}>
                  <Icon className="h-5 w-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className={cn('truncate text-sm', c.unread ? 'font-semibold' : 'font-medium')}>
                      {c.sender_name || 'Unknown'}
                    </span>
                    {c.e2ee && (
                      <span title="End-to-end encrypted">
                        <Lock className="h-3 w-3 text-muted-foreground" />
                      </span>
                    )}
                    {c.unread && <span className="h-2 w-2 shrink-0 rounded-full bg-blue-500" />}
                  </div>
                  <p className={cn('truncate text-sm', c.unread ? 'text-foreground' : 'text-muted-foreground')}>
                    {c.preview || 'No preview available'}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {meta.label} · {c.account_name}
                  </p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <span className="text-xs text-muted-foreground">{relativeTime(c.timestamp)}</span>
                  {c.url ? (
                    <a href={c.url} target="_blank" rel="noopener noreferrer">
                      <Button variant="ghost" size="sm">
                        <ExternalLink className="h-3.5 w-3.5" />
                        <span className="ml-1">Open</span>
                      </Button>
                    </a>
                  ) : c.platform === 'messenger' || c.platform === 'personal_messenger' ? (
                    <Link href="/messenger">
                      <Button variant="ghost" size="sm">
                        <span>Open</span>
                      </Button>
                    </Link>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
