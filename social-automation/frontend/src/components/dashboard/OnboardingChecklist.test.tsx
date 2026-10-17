import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { OnboardingChecklist } from '@/components/dashboard/OnboardingChecklist'

vi.mock('@/hooks/useQueries', () => ({
  useAccounts: vi.fn(() => ({ data: [] })),
  useBrand: vi.fn(() => ({ data: null })),
  useScheduledPosts: vi.fn(() => ({ data: [] })),
  useOverviewMetrics: vi.fn(() => ({ data: { total_posts: 0, scheduled_posts: 0 } })),
}))

describe('OnboardingChecklist', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('counts brand as complete when hasBrand is true', () => {
    render(
      <OnboardingChecklist
        connectedAccounts={0}
        hasBrand
        postCount={0}
        hasScheduledPost={false}
      />
    )

    expect(screen.getByText('Getting Started')).toBeInTheDocument()
    expect(screen.getByText('1 of 4 complete')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add brand basics' })).toBeInTheDocument()
  })

  it('auto-hides when all items are complete', () => {
    const { container } = render(
      <OnboardingChecklist
        connectedAccounts={1}
        hasBrand
        postCount={1}
        hasScheduledPost
      />
    )

    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByText('Getting Started')).not.toBeInTheDocument()
  })

  it('dismisses and remembers the dismissal', () => {
    const props = { connectedAccounts: 0, hasBrand: false, postCount: 0, hasScheduledPost: false }
    const { container, unmount } = render(<OnboardingChecklist {...props} />)
    fireEvent.click(screen.getAllByRole('button')[0])
    expect(container).toBeEmptyDOMElement()
    expect(localStorage.getItem('onboarding-checklist-dismissed')).toBe('true')
    unmount()
    const again = render(<OnboardingChecklist {...props} />)
    expect(again.container).toBeEmptyDOMElement()
  })
})
