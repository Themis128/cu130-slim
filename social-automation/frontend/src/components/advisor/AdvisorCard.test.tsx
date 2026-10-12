import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'

const advisor = vi.hoisted(() => ({
  suggestion: null as null | { id: string; icon: string; title: string; body: string; action?: { label: string; href: string } },
  dismiss: vi.fn(),
}))

vi.mock('@/hooks/useAdvisor', () => ({ useAdvisor: () => advisor }))

import { AdvisorCard } from './AdvisorCard'

describe('AdvisorCard', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    advisor.suggestion = null
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders nothing without a suggestion', () => {
    const { container } = render(<AdvisorCard />)
    expect(container.firstChild).toBeNull()
  })

  it('animates in and renders suggestion content with an action link', () => {
    advisor.suggestion = {
      id: 's1',
      icon: '🔗',
      title: 'Connect account',
      body: 'Do it now',
      action: { label: 'Connect now', href: '/accounts' },
    }
    render(<AdvisorCard />)
    act(() => { vi.advanceTimersByTime(100) })

    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.getByText('Connect account')).toBeInTheDocument()
    expect(screen.getByText('Do it now')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Connect now' })).toHaveAttribute('href', '/accounts')
    expect(screen.getByText('Smart suggestion')).toBeInTheDocument()
  })

  it('calls dismiss from the X button and the action button', () => {
    advisor.suggestion = {
      id: 's1', icon: 'i', title: 'T', body: 'B',
      action: { label: 'Go', href: '/x' },
    }
    render(<AdvisorCard />)
    act(() => { vi.advanceTimersByTime(100) })
    fireEvent.click(screen.getByLabelText('Dismiss suggestion'))
    expect(advisor.dismiss).toHaveBeenCalled()
  })

  it('renders without an action row when none is provided', () => {
    advisor.suggestion = { id: 's1', icon: 'i', title: 'T', body: 'B' }
    render(<AdvisorCard />)
    act(() => { vi.advanceTimersByTime(100) })
    expect(screen.getByText('T')).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
  })
})
