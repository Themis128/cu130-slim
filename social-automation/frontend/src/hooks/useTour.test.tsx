import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import { TourProvider, useTour, TOUR_STEPS } from './useTour'

function Probe() {
  const tour = useTour()
  return (
    <div>
      <span data-testid="open">{String(tour.isOpen)}</span>
      <span data-testid="step">{tour.currentStep}</span>
      <button onClick={tour.start}>start</button>
      <button onClick={tour.next}>next</button>
      <button onClick={tour.prev}>prev</button>
      <button onClick={tour.skip}>skip</button>
    </div>
  )
}

describe('TourProvider', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('auto-opens after 900ms when the tour was never completed', () => {
    render(<TourProvider><Probe /></TourProvider>)
    expect(screen.getByTestId('open').textContent).toBe('false')
    act(() => { vi.advanceTimersByTime(1000) })
    expect(screen.getByTestId('open').textContent).toBe('true')
  })

  it('stays closed when the tour was already completed', () => {
    localStorage.setItem('tour_completed', 'true')
    render(<TourProvider><Probe /></TourProvider>)
    act(() => { vi.advanceTimersByTime(2000) })
    expect(screen.getByTestId('open').textContent).toBe('false')
  })

  it('start resets to step 0 and opens', () => {
    render(<TourProvider><Probe /></TourProvider>)
    act(() => { vi.advanceTimersByTime(1000) })
    act(() => { fireEvent.click(screen.getByText('next')) })
    expect(screen.getByTestId('step').textContent).toBe('1')
    act(() => { fireEvent.click(screen.getByText('start')) })
    expect(screen.getByTestId('step').textContent).toBe('0')
    expect(screen.getByTestId('open').textContent).toBe('true')
  })

  it('next/prev step through, and the last next closes + persists completion', () => {
    render(<TourProvider><Probe /></TourProvider>)
    act(() => { fireEvent.click(screen.getByText('start')) })

    // prev clamps at 0
    act(() => { fireEvent.click(screen.getByText('prev')) })
    expect(screen.getByTestId('step').textContent).toBe('0')

    for (let i = 0; i < TOUR_STEPS.length - 1; i++) {
      act(() => { fireEvent.click(screen.getByText('next')) })
    }
    expect(screen.getByTestId('step').textContent).toBe(String(TOUR_STEPS.length - 1))
    expect(screen.getByTestId('open').textContent).toBe('true')

    // final next completes the tour
    act(() => { fireEvent.click(screen.getByText('next')) })
    expect(screen.getByTestId('open').textContent).toBe('false')
    expect(localStorage.getItem('tour_completed')).toBe('true')
  })

  it('skip closes and marks completed', () => {
    render(<TourProvider><Probe /></TourProvider>)
    act(() => { fireEvent.click(screen.getByText('start')) })
    act(() => { fireEvent.click(screen.getByText('skip')) })
    expect(screen.getByTestId('open').textContent).toBe('false')
    expect(localStorage.getItem('tour_completed')).toBe('true')
  })

  it('useTour falls back to a NOOP outside the provider', () => {
    render(<Probe />)
    expect(screen.getByTestId('open').textContent).toBe('false')
    // NOOP handlers are callable without errors
    fireEvent.click(screen.getByText('next'))
    fireEvent.click(screen.getByText('skip'))
  })
})

describe('useTour outside a provider', () => {
  it('returns an inert tour whose actions are safe no-ops', () => {
    render(<Probe />)
    for (const label of ['start', 'next', 'prev', 'skip']) {
      fireEvent.click(screen.getByText(label))
    }
    expect(screen.getByTestId('open').textContent).toBe('false')
    expect(screen.getByTestId('step').textContent).toBe('0')
    expect(localStorage.getItem('tour_completed')).toBeNull()
  })

  it('cancels the auto-open timer when unmounted early', () => {
    vi.useFakeTimers()
    localStorage.clear()
    const clear = vi.spyOn(globalThis, 'clearTimeout')
    const { unmount } = render(<TourProvider><Probe /></TourProvider>)
    unmount()
    expect(clear).toHaveBeenCalled()
    clear.mockRestore()
    vi.useRealTimers()
  })
})

