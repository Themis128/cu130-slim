import { vi, describe, it, expect, beforeEach } from 'vitest'
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
})
