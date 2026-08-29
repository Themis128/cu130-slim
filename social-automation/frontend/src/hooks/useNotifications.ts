'use client'

import { useState, useMemo, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { formatDistanceToNow } from 'date-fns'
import { contentApi, accountsApi } from '@/services/api'
import type { Post, SocialAccount } from '@/types'

export type NotificationType = 'success' | 'error' | 'warning' | 'info'

export interface AppNotification {
  id: string
  type: NotificationType
  title: string
  message: string
  time: string
  href?: string
  read: boolean
}

const LS_KEY = 'sa_read_nids'
const LS_DISMISS_KEY = 'sa_dismissed_nids'

function loadSet(key: string): Set<string> {
  if (typeof window === 'undefined') return new Set()
  try {
    const raw = localStorage.getItem(key)
    return new Set(raw ? (JSON.parse(raw) as string[]) : [])
  } catch {
    return new Set()
  }
}

function saveSet(key: string, ids: Set<string>): void {
  try {
    localStorage.setItem(key, JSON.stringify(Array.from(ids).slice(-300)))
  } catch { /* storage quota */ }
}

const loadReadIds = () => loadSet(LS_KEY)
const saveReadIds = (ids: Set<string>) => saveSet(LS_KEY, ids)

export function useNotifications() {
  const [readIds, setReadIds] = useState<Set<string>>(loadReadIds)
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(() => loadSet(LS_DISMISS_KEY))

  const { data: postsRaw } = useQuery({
    queryKey: ['posts-notifications'],
    queryFn: () => contentApi.listPosts({ page_size: 50 }),
    select: (r) => r.data,
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const { data: accountsRaw } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
    select: (r) => r.data,
    refetchInterval: 120_000,
    staleTime: 60_000,
  })

  const posts = useMemo<Post[]>(() => {
    if (!postsRaw) return []
    const d = postsRaw as { posts?: Post[]; items?: Post[] } | Post[]
    return Array.isArray(d) ? d : (d.posts ?? d.items ?? [])
  }, [postsRaw])

  const accounts = useMemo<SocialAccount[]>(() => {
    if (!accountsRaw) return []
    const d = accountsRaw as { accounts?: SocialAccount[]; items?: SocialAccount[] } | SocialAccount[]
    return Array.isArray(d) ? d : (d.accounts ?? d.items ?? [])
  }, [accountsRaw])

  const notifications = useMemo<AppNotification[]>(() => {
    const items: AppNotification[] = []
    const cutoff = Date.now() - 24 * 60 * 60 * 1000

    for (const p of posts) {
      if (p.status === 'failed') {
        const nid = `post-failed-${p.id}`
        const snippet = (p.content_text ?? '').slice(0, 60)
        items.push({
          id: nid,
          type: 'error',
          title: 'Post failed to publish',
          message: snippet + (p.failure_reason ? ` — ${p.failure_reason}` : ''),
          time: p.failed_at ? formatDistanceToNow(new Date(p.failed_at), { addSuffix: true }) : 'recently',
          href: `/content/${p.id}/edit`,
          read: readIds.has(nid),
        })
      } else if (p.status === 'published' && p.published_at && new Date(p.published_at).getTime() > cutoff) {
        const nid = `post-published-${p.id}`
        const snippet = (p.content_text ?? 'Your post').slice(0, 55)
        const platforms = Array.from(new Set(
          (p.targets ?? []).map(t => t.platform ?? t.social_account?.platform).filter((v): v is string => !!v)
        ))
        const platformStr = platforms.length > 0 ? ` on ${platforms.join(', ')}` : ''
        items.push({
          id: nid,
          type: 'success',
          title: 'Post published',
          message: `"${snippet}"${platformStr}`,
          time: formatDistanceToNow(new Date(p.published_at), { addSuffix: true }),
          href: `/content/${p.id}/edit`,
          read: readIds.has(nid),
        })
      }
    }

    for (const a of accounts) {
      if (a.status === 'expired' || a.status === 'error') {
        const nid = `account-${a.status}-${a.id}`
        items.push({
          id: nid,
          type: 'warning',
          title: a.status === 'expired' ? 'Account token expired' : 'Account connection error',
          message: `${a.display_name ?? a.username ?? a.platform} needs to be reconnected`,
          time: a.updated_at ? formatDistanceToNow(new Date(a.updated_at), { addSuffix: true }) : '',
          href: '/accounts',
          read: readIds.has(nid),
        })
      }
    }

    return items
      .filter(n => !dismissedIds.has(n.id))
      .sort((a, b) => {
        if (a.read !== b.read) return a.read ? 1 : -1
        return 0
      })
  }, [posts, accounts, readIds, dismissedIds])

  const unreadCount = useMemo(() => notifications.filter(n => !n.read).length, [notifications])

  const markAllRead = useCallback(() => {
    setReadIds(prev => {
      const next = new Set(prev)
      for (const n of notifications) next.add(n.id)
      saveReadIds(next)
      return next
    })
  }, [notifications])

  const markRead = useCallback((id: string) => {
    setReadIds(prev => {
      if (prev.has(id)) return prev
      const next = new Set(prev)
      next.add(id)
      saveReadIds(next)
      return next
    })
  }, [])

  const dismiss = useCallback((id: string) => {
    setDismissedIds(prev => {
      const next = new Set(prev)
      next.add(id)
      saveSet(LS_DISMISS_KEY, next)
      return next
    })
    // also mark read so the unread count drops immediately
    setReadIds(prev => {
      if (prev.has(id)) return prev
      const next = new Set(prev)
      next.add(id)
      saveReadIds(next)
      return next
    })
  }, [])

  return { notifications, unreadCount, markAllRead, markRead, dismiss }
}
