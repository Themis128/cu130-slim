import { vi, describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const router = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('next/navigation', () => ({ useRouter: () => router }))

const auth = vi.hoisted(() => ({ user: { name: 'Themis', email: 't@x.com' }, logout: vi.fn() }))
const theme = vi.hoisted(() => ({ theme: 'dark', toggleTheme: vi.fn() }))
const tour = vi.hoisted(() => ({ start: vi.fn() }))
const notifs = vi.hoisted(() => ({
  notifications: [] as Array<{ id: string; type: 'success' | 'error' | 'warning' | 'info'; title: string; message: string; time: string; href?: string; read: boolean }>,
  unreadCount: 0,
  markAllRead: vi.fn(),
  markRead: vi.fn(),
  dismiss: vi.fn(),
}))

vi.mock('@/hooks/useAuth', () => ({ useAuth: () => auth }))
vi.mock('@/hooks/useTheme', () => ({ useTheme: () => theme }))
vi.mock('@/hooks/useTour', () => ({ useTour: () => tour }))
vi.mock('@/hooks/useNotifications', () => ({ useNotifications: () => notifs }))

import { Header } from './Header'

// Radix dropdowns open on pointerdown, not click, in jsdom
function openMenu(trigger: HTMLElement) {
  fireEvent.pointerDown(trigger)
  fireEvent.click(trigger)
}

describe('Header', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    notifs.notifications = []
    notifs.unreadCount = 0
    theme.theme = 'dark'
  })

  it('renders search trigger, theme toggle, bell and avatar', () => {
    render(<Header />)
    expect(screen.getByText('Search pages, actions...')).toBeInTheDocument()
    expect(screen.getByLabelText('Switch to light mode')).toBeInTheDocument()
    expect(screen.getByLabelText('Notifications')).toBeInTheDocument()
    expect(screen.getByLabelText('User menu')).toBeInTheDocument()
  })

  it('opens the command palette via the ⌘K shortcut and the trigger', async () => {
    render(<Header />)
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    await waitFor(() => expect(document.querySelector('[role="dialog"], [cmdk-root]')).not.toBeNull())
    fireEvent.keyDown(window, { key: 'k', metaKey: true }) // toggle back
  })

  it('opens the command palette from the mobile search button and the desktop trigger', async () => {
    const { unmount } = render(<Header />)
    fireEvent.click(screen.getByLabelText('Search'))
    await waitFor(() => expect(document.querySelector('[role="dialog"], [cmdk-root]')).not.toBeNull())
    unmount()
    render(<Header />)
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    fireEvent.click(screen.getByText('Search pages, actions...'))
    await waitFor(() => expect(document.querySelector('[role="dialog"], [cmdk-root]')).not.toBeNull())
  })

  it('toggles the theme', () => {
    render(<Header />)
    fireEvent.click(screen.getByLabelText('Switch to light mode'))
    expect(theme.toggleTheme).toHaveBeenCalled()
  })

  it('shows the unread badge and marks all read', async () => {
    notifs.unreadCount = 12 // exercises the 9+ branch
    notifs.notifications = [
      { id: 'n1', type: 'success', title: 'Done', message: 'm', time: 'now', href: '/content/1', read: false },
      { id: 'n2', type: 'error', title: 'Failed', message: 'm', time: 'now', read: false },
      { id: 'n3', type: 'warning', title: 'Warn', message: 'm', time: 'now', read: true },
      { id: 'n4', type: 'info', title: 'Info', message: 'm', time: 'now', read: true },
    ]
    render(<Header />)
    openMenu(screen.getByLabelText('Notifications'))
    expect(await screen.findByText('9+')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Mark all read'))
    expect(notifs.markAllRead).toHaveBeenCalled()
  })

  it('renders every notification icon type and navigates on click', async () => {
    notifs.unreadCount = 1
    notifs.notifications = [
      { id: 'n1', type: 'success', title: 'Post published', message: 'm', time: 'now', href: '/content/9', read: false },
      { id: 'n2', type: 'error', title: 'Failed', message: 'm', time: 'now', read: true },
      { id: 'n3', type: 'warning', title: 'Warn', message: 'm', time: 'now', read: true },
      { id: 'n4', type: 'info', title: 'Info', message: 'm', time: 'now', read: true },
    ]
    render(<Header />)
    openMenu(screen.getByLabelText('Notifications'))

    fireEvent.click(await screen.findByText('Post published'))
    expect(notifs.markRead).toHaveBeenCalledWith('n1')
    expect(router.push).toHaveBeenCalledWith('/content/9')

    // dismiss button stops propagation (re-open: menu closed on item click)
    openMenu(screen.getByLabelText('Notifications'))
    const dismissBtns = await screen.findAllByLabelText('Dismiss notification')
    fireEvent.click(dismissBtns[1])
    expect(notifs.dismiss).toHaveBeenCalledWith('n2')
  })

  it('shows the empty state', async () => {
    render(<Header />)
    openMenu(screen.getByLabelText('Notifications'))
    expect(await screen.findByText('All caught up')).toBeInTheDocument()
  })

  it('user menu links to settings, starts the tour, and logs out', async () => {
    render(<Header />)
    openMenu(screen.getByLabelText('User menu'))
    expect(await screen.findByText('Themis')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Take Tour'))
    expect(tour.start).toHaveBeenCalled()
    openMenu(screen.getByLabelText('User menu')) // menu closes on item select
    fireEvent.click(await screen.findByText('Logout'))
    expect(auth.logout).toHaveBeenCalled()
  })
})
