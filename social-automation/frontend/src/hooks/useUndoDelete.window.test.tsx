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

type RenderFn = (t: { visible: boolean }) => JSX.Element

describe('useUndoDelete — undo window closes when the delete is sent', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('a late Undo after the delete went through does not restore the item', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useUndoDelete(onDelete))
    act(() => result.current.deleteWithUndo({ id: 'p1' }, 'Post'))
    const renderFn = toast.custom.mock.calls[0][0] as RenderFn
    // The toast is still mounted; react-hot-toast keeps it through the exit.
    render(<>{renderFn({ visible: true })}</>)

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    expect(onDelete).toHaveBeenCalledTimes(1)
    toast.dismiss.mockClear()

    act(() => fireEvent.click(screen.getByText('Undo')))

    expect(result.current.pendingIds.has('p1')).toBe(true) // stays hidden
    expect(toast.dismiss).not.toHaveBeenCalled()
    expect(onDelete).toHaveBeenCalledTimes(1)
  })

  it('an Undo while the delete request is still in flight does not restore it', async () => {
    let finish!: () => void
    const onDelete = vi.fn(() => new Promise<void>((r) => { finish = r }))
    const { result } = renderHook(() => useUndoDelete(onDelete))
    act(() => result.current.deleteWithUndo({ id: 'p2' }, 'Post'))
    const renderFn = toast.custom.mock.calls[0][0] as RenderFn
    render(<>{renderFn({ visible: true })}</>)

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    expect(onDelete).toHaveBeenCalledTimes(1) // request sent, not resolved

    act(() => fireEvent.click(screen.getByText('Undo')))
    expect(result.current.pendingIds.has('p2')).toBe(true)

    await act(async () => {
      finish()
    })
    expect(result.current.pendingIds.has('p2')).toBe(true)
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('a failed delete still brings the item back, since the server kept it', async () => {
    const onDelete = vi.fn().mockRejectedValue(new Error('500'))
    const { result } = renderHook(() => useUndoDelete(onDelete))
    act(() => result.current.deleteWithUndo({ id: 'p3' }, 'Post'))

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })

    expect(result.current.pendingIds.has('p3')).toBe(false)
    expect(toast.error).toHaveBeenCalledWith('Failed to delete post')
  })

  it('the Undo button is disabled while the toast animates out', () => {
    const { result } = renderHook(() => useUndoDelete(vi.fn().mockResolvedValue(undefined)))
    act(() => result.current.deleteWithUndo({ id: 'p4' }, 'Post'))
    const renderFn = toast.custom.mock.calls[0][0] as RenderFn

    const { rerender } = render(<>{renderFn({ visible: true })}</>)
    expect(screen.getByText('Undo').closest('button')).toBeEnabled()

    rerender(<>{renderFn({ visible: false })}</>)
    expect(screen.getByText('Undo').closest('button')).toBeDisabled()
  })

  it('Undo inside the window still cancels the delete', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useUndoDelete(onDelete))
    act(() => result.current.deleteWithUndo({ id: 'p5' }, 'Post'))
    const renderFn = toast.custom.mock.calls[0][0] as RenderFn
    render(<>{renderFn({ visible: true })}</>)

    await act(async () => {
      vi.advanceTimersByTime(4999)
    })
    act(() => fireEvent.click(screen.getByText('Undo')))
    await act(async () => {
      vi.advanceTimersByTime(10_000)
    })

    expect(result.current.pendingIds.has('p5')).toBe(false)
    expect(onDelete).not.toHaveBeenCalled()
  })
})
