import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { renderHook, act, render, screen, fireEvent } from '@testing-library/react'

const toast = vi.hoisted(() => ({
  custom: vi.fn(),
  dismiss: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
}))

vi.mock('react-hot-toast', () => ({ default: toast }))

import { useUndoDelete } from './useUndoDelete'

describe('useUndoDelete', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('hides the item immediately and commits after the delay', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useUndoDelete(onDelete))

    act(() => result.current.deleteWithUndo({ id: 'i1' }, 'Post'))
    expect(result.current.pendingIds.has('i1')).toBe(true)
    expect(toast.custom).toHaveBeenCalledWith(expect.any(Function), { id: 'i1', duration: 5400 })
    expect(onDelete).not.toHaveBeenCalled()

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    expect(onDelete).toHaveBeenCalledWith({ id: 'i1' })
    expect(toast.dismiss).toHaveBeenCalledWith('i1')
  })

  it('undo restores the item and cancels the delete', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useUndoDelete(onDelete))

    act(() => result.current.deleteWithUndo({ id: 'i1' }, 'Post'))

    // Render the toast content the hook passed to toast.custom and click Undo
    const renderFn = toast.custom.mock.calls[0][0] as (t: { visible: boolean }) => JSX.Element
    render(<>{renderFn({ visible: true })}</>)
    expect(screen.getByText('Post deleted')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Undo'))

    expect(result.current.pendingIds.has('i1')).toBe(false)
    expect(toast.dismiss).toHaveBeenCalledWith('i1')

    await act(async () => {
      vi.advanceTimersByTime(10000)
    })
    expect(onDelete).not.toHaveBeenCalled()
  })

  it('restores the item and toasts an error when the delete fails', async () => {
    const onDelete = vi.fn().mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useUndoDelete(onDelete))

    act(() => result.current.deleteWithUndo({ id: 'i1' }, 'Media'))
    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    expect(result.current.pendingIds.has('i1')).toBe(false)
    expect(toast.error).toHaveBeenCalledWith('Failed to delete media')
  })

  it('tracks multiple pending deletes independently', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useUndoDelete(onDelete))

    act(() => {
      result.current.deleteWithUndo({ id: 'a' }, 'Post')
      result.current.deleteWithUndo({ id: 'b' }, 'Post')
    })
    expect(result.current.pendingIds.size).toBe(2)

    // Undo only 'a'
    const renderA = toast.custom.mock.calls[0][0] as (t: { visible: boolean }) => JSX.Element
    render(<>{renderA({ visible: true })}</>)
    fireEvent.click(screen.getAllByText('Undo')[0])

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    expect(onDelete).toHaveBeenCalledTimes(1)
    expect(onDelete).toHaveBeenCalledWith({ id: 'b' })
  })
})

describe('useUndoDelete — toast exit animation', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders the hidden (exiting) state when react-hot-toast marks it not visible', () => {
    const { result } = renderHook(() => useUndoDelete(vi.fn().mockResolvedValue(undefined)))
    act(() => result.current.deleteWithUndo({ id: 'z1' }, 'Draft'))
    const renderFn = toast.custom.mock.calls[0][0] as (t: { visible: boolean }) => JSX.Element
    const { container, rerender } = render(<>{renderFn({ visible: false })}</>)
    const card = container.firstElementChild as HTMLElement
    expect(card.className).toContain('opacity-0')
    expect(card.className).toContain('translate-y-2')
    expect(screen.getByText('Draft deleted')).toBeInTheDocument()
    rerender(<>{renderFn({ visible: true })}</>)
    expect((container.firstElementChild as HTMLElement).className).toContain('opacity-100')
  })
})
