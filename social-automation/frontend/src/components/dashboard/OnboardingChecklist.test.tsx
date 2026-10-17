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

describe('OnboardingChecklist — completion from live query data', () => {
  beforeEach(async () => {
    localStorage.clear()
    const q = await import('@/hooks/useQueries')
    vi.mocked(q.useAccounts).mockReturnValue({ data: undefined } as never)
    vi.mocked(q.useBrand).mockReturnValue({ data: undefined } as never)
    vi.mocked(q.useScheduledPosts).mockReturnValue({ data: undefined } as never)
    vi.mocked(q.useOverviewMetrics).mockReturnValue({ data: undefined } as never)
  })

  const done = (label: string) => screen.getByText(label).classList.contains('line-through')

  it('treats still-loading queries and missing props as nothing done', () => {
    render(<OnboardingChecklist />)
    expect(screen.getByText('0 of 4 complete')).toBeInTheDocument()
    expect(screen.getByText('0%')).toBeInTheDocument()
    expect(['Connect a channel', 'Add brand basics', 'Create your first post', 'Schedule a post'].some(done)).toBe(false)
  })

  it('marks steps done from accounts, brand and overview totals without any props', async () => {
    const q = await import('@/hooks/useQueries')
    vi.mocked(q.useAccounts).mockReturnValue({ data: [{ id: 'a' }, { id: 'b' }] } as never)
    vi.mocked(q.useBrand).mockReturnValue({ data: { id: 'brand' } } as never)
    vi.mocked(q.useOverviewMetrics).mockReturnValue({ data: { total_posts: 7, scheduled_posts: 0 } } as never)
    render(<OnboardingChecklist postCount={2} connectedAccounts={1} />)
    expect(screen.getByText('3 of 4 complete')).toBeInTheDocument()
    expect(screen.getByText('75%')).toBeInTheDocument()
    expect(done('Connect a channel') && done('Add brand basics') && done('Create your first post')).toBe(true)
    expect(done('Schedule a post')).toBe(false)
    expect(screen.getByRole('link', { name: 'Schedule a post' })).toHaveAttribute('href', '/calendar')
  })

  it('counts scheduling from the overview even when the scheduled list is empty', async () => {
    const q = await import('@/hooks/useQueries')
    vi.mocked(q.useScheduledPosts).mockReturnValue({ data: [] } as never)
    vi.mocked(q.useOverviewMetrics).mockReturnValue({ data: { total_posts: 0, scheduled_posts: 3 } } as never)
    render(<OnboardingChecklist />)
    expect(done('Schedule a post')).toBe(true)
    expect(screen.getByText('1 of 4 complete')).toBeInTheDocument()
  })

  it('counts scheduling from the scheduled-posts list', async () => {
    const q = await import('@/hooks/useQueries')
    vi.mocked(q.useScheduledPosts).mockReturnValue({ data: [{ id: 'p1' }] } as never)
    render(<OnboardingChecklist />)
    expect(done('Schedule a post')).toBe(true)
  })
})
