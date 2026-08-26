'use client'

import { useRef, useState, useCallback } from 'react'
import toast from 'react-hot-toast'
import { Undo2 } from 'lucide-react'

const DELAY = 5000

// Animated drain bar + Undo button rendered inside the toast
function UndoToast({
  visible,
  label,
  onUndo,
}: {
  visible: boolean
  label: string
  onUndo: () => void
}) {
  return (
    <div
      className={[
        'flex flex-col w-72 rounded-lg border bg-background shadow-lg overflow-hidden',
        'transition-all duration-200',
        visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2',
      ].join(' ')}
    >
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <span className="text-sm font-medium">{label} deleted</span>
        <button
          onClick={onUndo}
          className="flex items-center gap-1.5 text-sm font-semibold text-primary hover:opacity-80 transition-opacity"
        >
          <Undo2 className="h-3.5 w-3.5" />
          Undo
        </button>
      </div>
      {/* drain bar */}
      <div className="h-0.5 bg-muted w-full">
        <div
          className="h-full bg-primary origin-left"
          style={{
            animation: `undo-drain ${DELAY}ms linear forwards`,
          }}
        />
      </div>
      <style>{`
        @keyframes undo-drain {
          from { width: 100%; }
          to   { width: 0%; }
        }
      `}</style>
    </div>
  )
}

/**
 * Optimistically hides `item` from UI immediately, then calls `onConfirmedDelete`
 * after DELAY ms unless the user clicks Undo.
 *
 * Usage:
 *   const { deleteWithUndo, pendingIds } = useUndoDelete(async (item) => {
 *     await api.delete(item.id)
 *     queryClient.invalidateQueries(...)
 *   })
 *
 *   // filter display:
 *   const visible = items.filter(i => !pendingIds.has(i.id))
 *
 *   // trigger:
 *   deleteWithUndo(item, 'Post')
 */
export function useUndoDelete<T extends { id: string }>(
  onConfirmedDelete: (item: T) => Promise<void>
) {
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set())
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  const restore = useCallback((id: string) => {
    setPendingIds((prev) => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }, [])

  const deleteWithUndo = useCallback(
    (item: T, label: string) => {
      const { id } = item

      // Immediately hide from UI
      setPendingIds((prev) => new Set(Array.from(prev).concat(id)))

      const undo = () => {
        const timer = timers.current.get(id)
        if (timer) clearTimeout(timer)
        timers.current.delete(id)
        restore(id)
        toast.dismiss(id)
      }

      const commit = async () => {
        timers.current.delete(id)
        toast.dismiss(id)
        try {
          await onConfirmedDelete(item)
        } catch {
          restore(id)
          toast.error(`Failed to delete ${label.toLowerCase()}`)
        }
      }

      const timer = setTimeout(commit, DELAY)
      timers.current.set(id, timer)

      toast.custom(
        (t) => <UndoToast visible={t.visible} label={label} onUndo={undo} />,
        { id, duration: DELAY + 400 }
      )
    },
    [onConfirmedDelete, restore]
  )

  return { deleteWithUndo, pendingIds }
}
