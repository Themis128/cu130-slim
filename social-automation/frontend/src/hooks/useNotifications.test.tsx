import { vi, describe, it, expect, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactNode } from 'react'

const hoisted = vi.hoisted(() => ({
  listPosts: vi.fn(),
  listAccounts: vi.fn(),
}))

vi.mock('@/services/api', () => ({
  contentApi: { listPosts: hoisted.listPosts },
  accountsApi: { list: hoisted.listAccounts },
}))

import { useNotifications } from './useNotifications'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const recentIso = () => new Date(Date.now() - 60_000).toISOString()
const oldIso = () => new Date(Date.now() - 3 * 24 * 3600_000).toISOString()

describe('useNotifications', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    hoisted.listPosts.mockResolvedValue({ data: { posts: [] } })
    hoisted.listAccounts.mockResolvedValue({ data: { accounts: [] } })
  })

  it('builds an error notification for failed posts', async () => {
    hoisted.listPosts.mockResolvedValue({
      data: { posts: [{ id: 'p1', status: 'failed', content_text: 'hello world', failure_reason: 'token expired', failed_at: recentIso() }] },
    })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(1))
    const n = result.current.notifications[0]
    expect(n.id).toBe('post-failed-p1')
    expect(n.type).toBe('error')
    expect(n.message).toContain('token expired')
    expect(n.href).toBe('/content/p1/edit')
    expect(result.current.unreadCount).toBe(1)
  })

  it('builds a success notification only for posts published in the last 24h', async () => {
    hoisted.listPosts.mockResolvedValue({
      data: { posts: [
        { id: 'p1', status: 'published', content_text: 'fresh', published_at: recentIso(), targets: [{ platform: 'x' }, { platform: 'linkedin' }] },
        { id: 'p2', status: 'published', content_text: 'stale', published_at: oldIso() },
      ] },
    })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(1))
    const n = result.current.notifications[0]
    expect(n.id).toBe('post-published-p1')
    expect(n.message).toContain('on x, linkedin')
  })

  it('builds warning notifications for expired/error accounts', async () => {
    hoisted.listAccounts.mockResolvedValue({
      data: { accounts: [
        { id: 'a1', status: 'expired', platform: 'linkedin', display_name: 'LI' },
        { id: 'a2', status: 'error', platform: 'x', username: 'tw' },
        { id: 'a3', status: 'active', platform: 'threads' },
      ] },
    })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(2))
    const titles = result.current.notifications.map(n => n.title)
    expect(titles).toContain('Account token expired')
    expect(titles).toContain('Account connection error')
  })

  it('accepts array-shaped responses for posts and accounts', async () => {
    hoisted.listPosts.mockResolvedValue({ data: [{ id: 'p1', status: 'failed', content_text: 'x' }] })
    hoisted.listAccounts.mockResolvedValue({ data: [{ id: 'a1', status: 'expired', platform: 'x' }] })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(2))
  })

  it('markRead and markAllRead update unread count and persist', async () => {
    hoisted.listPosts.mockResolvedValue({
      data: { posts: [
        { id: 'p1', status: 'failed', content_text: 'a' },
        { id: 'p2', status: 'failed', content_text: 'b' },
      ] },
    })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.unreadCount).toBe(2))

    act(() => result.current.markRead('post-failed-p1'))
    await waitFor(() => expect(result.current.unreadCount).toBe(1))
    expect(JSON.parse(localStorage.getItem('sa_read_nids')!)).toContain('post-failed-p1')

    act(() => result.current.markAllRead())
    await waitFor(() => expect(result.current.unreadCount).toBe(0))
  })

  it('unread notifications sort before read ones', async () => {
    localStorage.setItem('sa_read_nids', JSON.stringify(['post-failed-p1']))
    hoisted.listPosts.mockResolvedValue({
      data: { posts: [
        { id: 'p1', status: 'failed', content_text: 'a' },
        { id: 'p2', status: 'failed', content_text: 'b' },
      ] },
    })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(2))
    expect(result.current.notifications[0].id).toBe('post-failed-p2')
    expect(result.current.notifications[0].read).toBe(false)
  })

  it('dismiss removes the notification and marks it read', async () => {
    hoisted.listPosts.mockResolvedValue({
      data: { posts: [{ id: 'p1', status: 'failed', content_text: 'a' }] },
    })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(1))

    act(() => result.current.dismiss('post-failed-p1'))
    await waitFor(() => expect(result.current.notifications).toHaveLength(0))
    expect(JSON.parse(localStorage.getItem('sa_dismissed_nids')!)).toContain('post-failed-p1')
    expect(result.current.unreadCount).toBe(0)
  })

  it('dismissed notifications stay hidden on remount', async () => {
    localStorage.setItem('sa_dismissed_nids', JSON.stringify(['post-failed-p1']))
    hoisted.listPosts.mockResolvedValue({
      data: { posts: [{ id: 'p1', status: 'failed', content_text: 'a' }] },
    })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(0))
  })
})
