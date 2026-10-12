import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { AdvisorProvider, useAdvisor, type AdvisorCtx } from './useAdvisor'

let mockPathname = '/dashboard'

vi.mock('next/navigation', () => ({
  usePathname: () => mockPathname,
}))

function Probe({ onValue }: { onValue: (v: ReturnType<typeof useAdvisor>) => void }) {
  const advisor = useAdvisor()
  onValue(advisor)
  return null
}

function mount() {
  let latest!: ReturnType<typeof useAdvisor>
  render(
    <AdvisorProvider>
      <Probe onValue={(v) => { latest = v }} />
    </AdvisorProvider>
  )
  return () => latest
}

describe('AdvisorProvider rule engine', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    sessionStorage.clear()
    mockPathname = '/dashboard'
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  const setCtxAndTick = (get: () => ReturnType<typeof useAdvisor>, patch: Partial<AdvisorCtx>) => {
    act(() => get().setCtx(patch))
    act(() => { vi.advanceTimersByTime(2000) })
  }

  it('suggests connecting an account on dashboard with zero accounts', () => {
    const get = mount()
    setCtxAndTick(get, { connectedAccountsCount: 0 })
    expect(get().suggestion?.id).toBe('no-accounts')
    expect(get().suggestion?.action?.href).toBe('/accounts')
  })

  it('suggests a schedule when published but nothing scheduled', () => {
    const get = mount()
    setCtxAndTick(get, { connectedAccountsCount: 3, publishedCount: 5, scheduledCount: 0 })
    expect(get().suggestion?.id).toBe('no-schedule')
  })

  it('stays silent when posts are scheduled', () => {
    const get = mount()
    setCtxAndTick(get, { connectedAccountsCount: 3, publishedCount: 5, scheduledCount: 2 })
    expect(get().suggestion).toBeNull()
  })

  it('suggests picking a platform on the editor with content but none selected', () => {
    mockPathname = '/content/new'
    const get = mount()
    setCtxAndTick(get, { content: 'a'.repeat(30), selectedPlatforms: [] })
    expect(get().suggestion?.id).toBe('no-platforms')
  })

  it('warns near the Twitter character limit', () => {
    mockPathname = '/content/new'
    const get = mount()
    setCtxAndTick(get, {
      content: `${'a'.repeat(250)} #tag`,
      selectedPlatforms: ['twitter'],
      hasMedia: true,
      aiUsed: true,
    })
    expect(get().suggestion?.id).toBe('twitter-limit')
    expect(get().suggestion?.body).toContain('255 characters')
  })

  it('suggests hashtags for long content without them', () => {
    mockPathname = '/content/new'
    const get = mount()
    setCtxAndTick(get, {
      content: 'a'.repeat(70),
      selectedPlatforms: ['linkedin'],
      hasMedia: true,
      aiUsed: true,
    })
    expect(get().suggestion?.id).toBe('no-hashtags')
  })

  it('suggests media for content with none attached', () => {
    mockPathname = '/content/new'
    const get = mount()
    setCtxAndTick(get, {
      content: `${'a'.repeat(40)} #tag`,
      selectedPlatforms: ['linkedin'],
      hasMedia: false,
      aiUsed: true,
    })
    expect(get().suggestion?.id).toBe('add-media')
  })

  it('suggests AI assist for long untouched drafts', () => {
    mockPathname = '/content/new'
    const get = mount()
    setCtxAndTick(get, {
      content: `${'a'.repeat(55)} #tag`,
      selectedPlatforms: ['linkedin'],
      hasMedia: true,
      aiUsed: false,
    })
    expect(get().suggestion?.id).toBe('try-ai')
  })

  it('dismiss persists the id and suppresses the rule', () => {
    const get = mount()
    setCtxAndTick(get, { connectedAccountsCount: 0 })
    expect(get().suggestion?.id).toBe('no-accounts')

    act(() => get().dismiss())
    expect(get().suggestion).toBeNull()

    // Re-render — dismissed rule stays suppressed via sessionStorage
    setCtxAndTick(get, { connectedAccountsCount: 0 })
    expect(get().suggestion).toBeNull()
  })

  it('clears editor context when navigating away from /content/new', () => {
    mockPathname = '/content/new'
    const get = mount()
    setCtxAndTick(get, { content: 'a'.repeat(30), selectedPlatforms: [] })
    expect(get().suggestion?.id).toBe('no-platforms')

    mockPathname = '/dashboard'
    // rerender by patching ctx to trigger the pathname effect
    act(() => { vi.advanceTimersByTime(10) })
    act(() => get().setCtx({ connectedAccountsCount: 1 }))
    act(() => { vi.advanceTimersByTime(2000) })
    // editor-only rules no longer fire on dashboard
    expect(get().suggestion?.id).not.toBe('no-platforms')
  })
})
