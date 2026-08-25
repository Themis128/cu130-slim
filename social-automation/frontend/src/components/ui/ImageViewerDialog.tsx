'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import {
  X,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Scaling,
  Download,
  FileWarning,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'

export interface ImageViewerItem {
  /** URL that serves the image (any browser-renderable format). */
  src: string
  alt?: string
  filename?: string
  mime_type?: string | null
  width?: number | null
  height?: number | null
  size_bytes?: number | null
}

const MIN_SCALE = 0.25
const MAX_SCALE = 8
const WHEEL_STEP = 1.15
const BUTTON_STEP = 1.4

function clampScale(s: number) {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, s))
}

function formatBytes(bytes?: number | null) {
  if (!bytes && bytes !== 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

interface ImageViewerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  item: ImageViewerItem | null
}

/**
 * Full-screen image lightbox with zoom & pan.
 *
 * - Zoom via toolbar buttons, mouse wheel, double-click or keyboard (+/−/0)
 * - Drag to pan when zoomed in; "Fit" and "1:1" presets in the toolbar
 * - Renders whatever the browser can display — pair it with the backend
 *   `/media/view` endpoint to guarantee previewable output for any upload.
 */
export function ImageViewerDialog({ open, onOpenChange, item }: ImageViewerDialogProps) {
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [failed, setFailed] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const dragState = useRef<{ startX: number; startY: number; baseX: number; baseY: number } | null>(null)

  // Reset transform whenever a new item is opened.
  useEffect(() => {
    if (open) {
      setScale(1)
      setOffset({ x: 0, y: 0 })
      setFailed(false)
    }
  }, [open, item?.src])

  const zoomBy = useCallback((factor: number) => {
    setScale((s) => clampScale(s * factor))
  }, [])

  // Clamp pan offset so the image can't be dragged fully out of view.
  useEffect(() => {
    setOffset((o) => {
      const el = containerRef.current
      if (!el || scale <= 1) return { x: 0, y: 0 }
      const maxX = (el.clientWidth * (scale - 1)) / 2
      const maxY = (el.clientHeight * (scale - 1)) / 2
      return {
        x: Math.min(maxX, Math.max(-maxX, o.x)),
        y: Math.min(maxY, Math.max(-maxY, o.y)),
      }
    })
  }, [scale])

  // Keyboard shortcuts while the viewer is open.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '+' || e.key === '=') zoomBy(BUTTON_STEP)
      else if (e.key === '-' || e.key === '_') zoomBy(1 / BUTTON_STEP)
      else if (e.key === '0' || e.key.toLowerCase() === 'r') {
        setScale(1)
        setOffset({ x: 0, y: 0 })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, zoomBy])

  // Non-passive wheel handler so we can preventDefault page scrolling.
  useEffect(() => {
    const el = containerRef.current
    if (!open || !el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      zoomBy(e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [open, zoomBy])

  const onPointerDown = (e: React.PointerEvent) => {
    ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
    dragState.current = { startX: e.clientX, startY: e.clientY, baseX: offset.x, baseY: offset.y }
  }
  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragState.current
    const el = containerRef.current
    if (!d || !el) return
    const maxX = (el.clientWidth * (scale - 1)) / 2
    const maxY = (el.clientHeight * (scale - 1)) / 2
    setOffset({
      x: Math.min(maxX, Math.max(-maxX, d.baseX + (e.clientX - d.startX))),
      y: Math.min(maxY, Math.max(-maxY, d.baseY + (e.clientY - d.startY))),
    })
  }
  const endDrag = () => {
    dragState.current = null
  }

  const isVideo = !!item?.mime_type?.startsWith('video/')
  const canZoom = !isVideo && !failed

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/90 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          className="fixed inset-0 z-50 flex flex-col focus:outline-none"
          aria-describedby={undefined}
        >
          {/* Toolbar */}
          <div className="flex items-center justify-between gap-2 border-b border-white/10 bg-black/70 px-4 py-2">
            <DialogPrimitive.Title className="truncate text-sm font-medium text-white/90">
              {item?.filename || 'Image viewer'}
            </DialogPrimitive.Title>
            <div className="flex items-center gap-1">
              {canZoom && (
                <>
                  <Button variant="ghost" size="icon" className="text-white hover:bg-white/15" title="Zoom out (−)"
                    onClick={() => zoomBy(1 / BUTTON_STEP)}>
                    <ZoomOut className="h-4 w-4" />
                  </Button>
                  <span className="w-14 text-center text-xs tabular-nums text-white/80">
                    {Math.round(scale * 100)}%
                  </span>
                  <Button variant="ghost" size="icon" className="text-white hover:bg-white/15" title="Zoom in (+)"
                    onClick={() => zoomBy(BUTTON_STEP)}>
                    <ZoomIn className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="icon" className="text-white hover:bg-white/15" title="Fit to screen (0)"
                    onClick={() => { setScale(1); setOffset({ x: 0, y: 0 }) }}>
                    <Maximize2 className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="icon" className="text-white hover:bg-white/15" title="Actual size (1:1)"
                    onClick={() => { setScale(clampScale(3)); setOffset({ x: 0, y: 0 }) }}>
                    <Scaling className="h-4 w-4" />
                  </Button>
                </>
              )}
              {item?.src && (
                <a
                  href={item.src}
                  download={item.filename || true}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-9 w-9 items-center justify-center rounded-md text-white transition-colors hover:bg-white/15"
                  title="Download / open original"
                >
                  <Download className="h-4 w-4" />
                </a>
              )}
              <DialogPrimitive.Close
                className="inline-flex h-9 w-9 items-center justify-center rounded-md text-white transition-colors hover:bg-white/15"
                title="Close (Esc)"
              >
                <X className="h-4 w-4" />
              </DialogPrimitive.Close>
            </div>
          </div>

          {/* Viewport */}
          <div
            ref={containerRef}
            className={cn(
              'relative flex flex-1 items-center justify-center overflow-hidden',
              canZoom ? (scale > 1 ? 'cursor-grab active:cursor-grabbing' : 'cursor-zoom-in') : '',
            )}
            onPointerDown={canZoom ? onPointerDown : undefined}
            onPointerMove={canZoom ? onPointerMove : undefined}
            onPointerUp={endDrag}
            onPointerLeave={endDrag}
            onDoubleClick={canZoom ? () => {
              setScale((s) => (s > 1 ? 1 : clampScale(3)))
              setOffset({ x: 0, y: 0 })
            } : undefined}
          >
            {!item ? null : failed ? (
              <div className="flex flex-col items-center gap-3 p-8 text-center text-white/80">
                <FileWarning className="h-10 w-10" />
                <p className="text-sm">
                  This file cannot be previewed in the browser.
                  <br />
                  Use the download button above to open the original.
                </p>
              </div>
            ) : isVideo ? (
              <video src={item.src} controls autoPlay className="max-h-[85vh] max-w-[92vw]" />
            ) : (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={item.src}
                alt={item.alt || item.filename || 'Media'}
                draggable={false}
                onError={() => setFailed(true)}
                className="max-h-[85vh] max-w-[92vw] select-none object-contain transition-transform duration-100 will-change-transform"
                style={{ transform: `scale(${scale}) translate(${offset.x / scale}px, ${offset.y / scale}px)` }}
              />
            )}
          </div>

          {/* Metadata footer */}
          {(item?.width || item?.height || item?.size_bytes || item?.mime_type) && (
            <div className="flex items-center gap-4 border-t border-white/10 bg-black/70 px-4 py-1.5 text-xs text-white/60">
              {item.width && item.height ? (
                <span>{item.width} × {item.height}px</span>
              ) : null}
              {item.mime_type ? <span>{item.mime_type}</span> : null}
              {item.size_bytes ? <span>{formatBytes(item.size_bytes)}</span> : null}
              <span className="ml-auto hidden sm:inline">Scroll to zoom · Drag to pan · Double-click for 300%</span>
            </div>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}