import { vi, describe, it, expect, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
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

  it('falls back to items[] and tolerates payloads with neither key', async () => {
    hoisted.listPosts.mockResolvedValue({ data: { items: [{ id: 'p1', status: 'failed' }] } })
    hoisted.listAccounts.mockResolvedValue({ data: { items: [{ id: 'a1', status: 'error', platform: 'x' }] } })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(2))

    hoisted.listPosts.mockResolvedValue({ data: {} })
    hoisted.listAccounts.mockResolvedValue({ data: {} })
    const { result: r2 } = renderHook(
      () => ({ n: useNotifications(), qc: useQueryClient() }),
      { wrapper },
    )
    await waitFor(() => {
      expect(r2.current.qc.getQueryState(['posts-notifications'])?.status).toBe('success')
      expect(r2.current.qc.getQueryState(['accounts'])?.status).toBe('success')
    })
    expect(r2.current.n.notifications).toEqual([])
  })

  it('sorts a read notification after an unread one regardless of source order', async () => {
    localStorage.setItem('sa_read_nids', JSON.stringify(['post-failed-first']))
    hoisted.listPosts.mockResolvedValue({
      data: [
        { id: 'first', status: 'failed' },
        { id: 'second', status: 'failed' },
      ],
    })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(2))
    expect(result.current.notifications.map((n) => n.id)).toEqual([
      'post-failed-second',
      'post-failed-first',
    ])
  })

  it('uses defaults for missing text, timestamps, platforms and names', async () => {
    hoisted.listPosts.mockResolvedValue({
      data: { posts: [
        // failed with no text/reason/timestamp
        { id: 'f', status: 'failed' },
        // published with no text and targets that carry no usable platform
        { id: 'p', status: 'published', published_at: recentIso(), targets: [{}, { social_account: { platform: 'threads' } }] },
        // published with no targets at all
        { id: 'q', status: 'published', content_text: 'solo', published_at: recentIso() },
      ] },
    })
    hoisted.listAccounts.mockResolvedValue({ data: { accounts: [{ id: 'a', status: 'expired', platform: 'tiktok' }] } })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(4))
    const byId = Object.fromEntries(result.current.notifications.map((n) => [n.id, n]))
    expect(byId['post-failed-f'].message).toBe('')
    expect(byId['post-failed-f'].time).toBe('recently')
    expect(byId['post-published-p'].message).toBe('"Your post" on threads')
    expect(byId['post-published-q'].message).toBe('"solo"')
    expect(byId['account-expired-a'].message).toBe('tiktok needs to be reconnected')
    expect(byId['account-expired-a'].time).toBe('')
  })

  it('shows relative time for account errors with updated_at', async () => {
    hoisted.listAccounts.mockResolvedValue({
      data: [{ id: 'a', status: 'error', username: 'cu', platform: 'x', updated_at: recentIso() }],
    })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(1))
    expect(result.current.notifications[0].time).toMatch(/ago$/)
    expect(result.current.notifications[0].message).toBe('cu needs to be reconnected')
  })

  it('markRead and dismiss are no-ops on already-read ids', async () => {
    localStorage.setItem('sa_read_nids', JSON.stringify(['post-failed-p1']))
    hoisted.listPosts.mockResolvedValue({ data: [{ id: 'p1', status: 'failed' }] })
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(1))
    expect(result.current.unreadCount).toBe(0)

    act(() => result.current.markRead('post-failed-p1'))
    expect(setItem).not.toHaveBeenCalledWith('sa_read_nids', expect.anything())

    act(() => result.current.dismiss('post-failed-p1'))
    expect(result.current.notifications).toEqual([])
    // dismissal is persisted; the read set is left untouched
    expect(setItem).toHaveBeenCalledWith('sa_dismissed_nids', JSON.stringify(['post-failed-p1']))
    expect(setItem).not.toHaveBeenCalledWith('sa_read_nids', expect.anything())
    setItem.mockRestore()
  })

  it('ignores corrupt localStorage state', async () => {
    localStorage.setItem('sa_read_nids', '{not json')
    hoisted.listPosts.mockResolvedValue({ data: [{ id: 'p1', status: 'failed' }] })
    const { result } = renderHook(() => useNotifications(), { wrapper })
    await waitFor(() => expect(result.current.notifications).toHaveLength(1))
    expect(result.current.unreadCount).toBe(1)
  })
})
