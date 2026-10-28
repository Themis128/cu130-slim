import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import { TourOverlay } from './TourOverlay'
import { TOUR_STEPS } from '@/hooks/useTour'

const router = { push: vi.fn() }
let mockPathname = '/dashboard'

vi.mock('next/navigation', () => ({
  useRouter: () => router,
  usePathname: () => mockPathname,
}))

const tour = {
  steps: TOUR_STEPS,
  currentStep: 0,
  isOpen: true,
  next: vi.fn(),
  prev: vi.fn(),
  skip: vi.fn(),
}

vi.mock('@/hooks/useTour', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useTour')>()
  return { ...actual, useTour: () => tour }
})

function addTarget(name: string) {
  const el = document.createElement('div')
  el.setAttribute('data-tour', name)
  el.getBoundingClientRect = () =>
    ({ top: 100, left: 100, width: 50, height: 30, right: 150, bottom: 130, x: 100, y: 100, toJSON: () => ({}) }) as DOMRect
  document.body.appendChild(el)
  return el
}

describe('TourOverlay', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    mockPathname = '/dashboard'
    tour.currentStep = 0
    tour.isOpen = true
  })

  it('renders nothing when the tour is closed', () => {
    tour.isOpen = false
    const { container } = render(<TourOverlay />)
    expect(container.firstChild).toBeNull()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
  })

  it('shows the step tooltip over a real target on the right page', async () => {
    addTarget('nav')
    render(<TourOverlay />)
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAttribute('aria-label', 'Tour step 1: Navigate the app')
    expect(screen.getByText('Navigate the app')).toBeInTheDocument()
    // progress dots = one dot per step
    expect(document.body.querySelectorAll('.bg-primary').length).toBeGreaterThan(0)
  })

  it('shows a navigating indicator when the step lives on another page', () => {
    // step 0 targets /dashboard; put us elsewhere
    mockPathname = '/content/new'
    render(<TourOverlay />)
    expect(screen.getByText('Navigating to the next step…')).toBeInTheDocument()
  })

  it('calls next() and router.push when the step navigates elsewhere', async () => {
    tour.currentStep = 4 // quick-actions → navigateTo /content/new
    addTarget('quick-actions')
    render(<TourOverlay />)
    const btn = await screen.findByText('Next →')
    fireEvent.click(btn)
    expect(router.push).toHaveBeenCalledWith('/content/new')
    expect(tour.next).toHaveBeenCalled()
  })

  it('calls skip() from the X button and the footer button', async () => {
    addTarget('nav')
    render(<TourOverlay />)
    fireEvent.click(await screen.findByLabelText('Skip tour'))
    fireEvent.click(screen.getByText('Skip tour'))
    expect(tour.skip).toHaveBeenCalledTimes(2)
  })

  it('shows Prev after step 0 and Done on the last step', async () => {
    tour.currentStep = 1
    addTarget('create-post')
    const { unmount } = render(<TourOverlay />)
    expect(await screen.findByRole('button', { name: '' })).toBeInTheDocument() // ArrowLeft icon button

    unmount()
    tour.currentStep = TOUR_STEPS.length - 1
    mockPathname = '/content/new'
    addTarget('publish-actions')
    render(<TourOverlay />)
    expect(await screen.findByText('Done')).toBeInTheDocument()
  })

  it('positions the tooltip per placement without crashing', async () => {
    for (const [idx, target] of [[2, 'stats'], [3, 'top-posts']] as const) {
      document.body.innerHTML = ''
      tour.currentStep = idx
      addTarget(target)
      const { unmount } = render(<TourOverlay />)
      await screen.findByRole('dialog')
      unmount()
    }
  })

  describe('target lookup and repositioning', () => {
    afterEach(() => {
      vi.useRealTimers()
    })

    function spotlight() {
      return document.body.querySelector<HTMLElement>('.pointer-events-none.rounded-lg')
    }

    it('keeps retrying until the target renders, then shows the tooltip', () => {
      vi.useFakeTimers()
      render(<TourOverlay />)
      // No target yet → no rect → neither spotlight nor tooltip.
      expect(document.body.querySelector('[role="dialog"]')).toBeNull()
      expect(spotlight()).toBeNull()
      act(() => { vi.advanceTimersByTime(360) }) // three misses
      expect(document.body.querySelector('[role="dialog"]')).toBeNull()

      addTarget('nav')
      act(() => { vi.advanceTimersByTime(120) })
      expect(screen.getByRole('dialog')).toHaveAttribute('aria-label', 'Tour step 1: Navigate the app')
      // placement "right": tooltip sits right of the padded rect (100-6 + 50+12 + 14)
      expect(screen.getByRole('dialog').style.left).toBe('170px')
    })

    it('cancels a pending retry when the step changes and when unmounting', () => {
      vi.useFakeTimers()
      const qs = vi.spyOn(document, 'querySelector')
      const { rerender, unmount } = render(<TourOverlay />)
      tour.currentStep = 1 // re-runs the effect while step 0's retry is pending
      rerender(<TourOverlay />)
      qs.mockClear()
      act(() => { vi.advanceTimersByTime(120) })
      const looked = qs.mock.calls.map((c) => c[0])
      expect(looked).toContain('[data-tour="create-post"]')
      expect(looked).not.toContain('[data-tour="nav"]') // stale retry was cleared

      unmount()
      qs.mockClear()
      act(() => { vi.advanceTimersByTime(1000) })
      expect(qs.mock.calls.filter((c) => String(c[0]).startsWith('[data-tour'))).toHaveLength(0)
      qs.mockRestore()
    })

    it('recomputes on resize and keeps the last rect if the target disappears', async () => {
      const el = addTarget('nav')
      render(<TourOverlay />)
      await screen.findByRole('dialog')
      expect(spotlight()!.style.top).toBe('94px')

      el.getBoundingClientRect = () =>
        ({ top: 300, left: 100, width: 50, height: 30, right: 150, bottom: 330, x: 100, y: 300, toJSON: () => ({}) }) as DOMRect
      act(() => { window.dispatchEvent(new Event('resize')) })
      expect(spotlight()!.style.top).toBe('294px')

      el.remove()
      act(() => { window.dispatchEvent(new Event('scroll')) })
      expect(spotlight()!.style.top).toBe('294px') // no target → rect unchanged
    })

    it('Next on a step without navigateTo only advances', async () => {
      addTarget('nav')
      render(<TourOverlay />)
      fireEvent.click(await screen.findByRole('button', { name: /^Next$/ }))
      expect(tour.next).toHaveBeenCalledTimes(1)
      expect(router.push).not.toHaveBeenCalled()
    })
  })
})
