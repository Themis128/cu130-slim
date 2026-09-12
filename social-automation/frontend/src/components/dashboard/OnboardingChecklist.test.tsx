import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
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
    expect(screen.getByRole('link', { name: 'Set up your brand' })).toBeInTheDocument()
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
})

