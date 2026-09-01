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
const BUTTON_STEP = 1.25

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
 * - Zoom via floating toolbar, mouse wheel, double-click, or keyboard (+/−/0)
 * - Drag to pan when zoomed in; Fit and 1:1 presets
 */
export function ImageViewerDialog({ open, onOpenChange, item }: ImageViewerDialogProps) {
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [failed, setFailed] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)
  const dragState = useRef<{ startX: number; startY: number; baseX: number; baseY: number } | null>(null)

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

  const fitToScreen = useCallback(() => {
    setScale(1)
    setOffset({ x: 0, y: 0 })
  }, [])

  /** Zoom so 1 image pixel ≈ 1 screen pixel (true 1:1). */
  const actualSize = useCallback(() => {
    const img = imgRef.current
    if (!img?.naturalWidth || !img.clientWidth) {
      setScale(clampScale(2))
      setOffset({ x: 0, y: 0 })
      return
    }
    setScale(clampScale(img.naturalWidth / img.clientWidth))
    setOffset({ x: 0, y: 0 })
  }, [])

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

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '+' || e.key === '=') zoomBy(BUTTON_STEP)
      else if (e.key === '-' || e.key === '_') zoomBy(1 / BUTTON_STEP)
      else if (e.key === '0' || e.key.toLowerCase() === 'r') fitToScreen()
      else if (e.key === '1') actualSize()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, zoomBy, fitToScreen, actualSize])

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
    if ((e.target as HTMLElement).closest('[data-zoom-toolbar]')) return
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
  const isPdf = item?.mime_type === 'application/pdf'
  const isAudio = !!item?.mime_type?.startsWith('audio/')
  const canZoom = !isVideo && !isPdf && !isAudio && !failed
  const pct = Math.round(scale * 100)

  const ZoomControls = (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-9 w-9 text-white hover:bg-white/15"
        title="Zoom out (−)"
        aria-label="Zoom out"
        onClick={(e) => { e.stopPropagation(); zoomBy(1 / BUTTON_STEP) }}
      >
        <ZoomOut className="h-4 w-4" />
      </Button>
      <button
        type="button"
        className="min-w-[3.25rem] rounded px-1 text-center text-xs tabular-nums text-white/90 hover:bg-white/10"
        title="Reset to fit (0)"
        onClick={(e) => { e.stopPropagation(); fitToScreen() }}
      >
        {pct}%
      </button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-9 w-9 text-white hover:bg-white/15"
        title="Zoom in (+)"
        aria-label="Zoom in"
        onClick={(e) => { e.stopPropagation(); zoomBy(BUTTON_STEP) }}
      >
        <ZoomIn className="h-4 w-4" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-9 w-9 text-white hover:bg-white/15"
        title="Fit to screen (0)"
        aria-label="Fit to screen"
        onClick={(e) => { e.stopPropagation(); fitToScreen() }}
      >
        <Maximize2 className="h-4 w-4" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-9 w-9 text-white hover:bg-white/15"
        title="Actual size 1:1 (1)"
        aria-label="Actual size"
        onClick={(e) => { e.stopPropagation(); actualSize() }}
      >
        <Scaling className="h-4 w-4" />
      </Button>
    </>
  )

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/90 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          className="fixed inset-0 z-50 flex flex-col focus:outline-none"
          aria-describedby={undefined}
        >
          <div className="flex items-center justify-between gap-2 border-b border-white/10 bg-black/70 px-4 py-2">
            <DialogPrimitive.Title className="truncate text-sm font-medium text-white/90">
              {item?.filename || 'Image viewer'}
            </DialogPrimitive.Title>
            <div className="flex items-center gap-1">
              {canZoom && (
                <div className="mr-1 hidden items-center gap-0.5 sm:flex" data-zoom-toolbar>
                  {ZoomControls}
                </div>
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
              setScale((s) => (s > 1.05 ? 1 : clampScale(2)))
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
            ) : isPdf ? (
              <iframe src={item.src} title={item.alt || item.filename || 'PDF'} className="h-[85vh] w-[92vw] border-0" />
            ) : isAudio ? (
              <div className="flex flex-col items-center gap-4 p-8">
                <audio src={item.src} controls autoPlay className="w-full max-w-md" />
                <p className="text-sm text-white/80">{item.alt || item.filename || 'Audio'}</p>
              </div>
            ) : (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                ref={imgRef}
                src={item.src}
                alt={item.alt || item.filename || 'Media'}
                draggable={false}
                onError={() => setFailed(true)}
                className="max-h-[85vh] max-w-[92vw] select-none object-contain transition-transform duration-100 will-change-transform"
                style={{ transform: `scale(${scale}) translate(${offset.x / scale}px, ${offset.y / scale}px)` }}
              />
            )}

            {/* Floating zoom tool — always visible on mobile / easy to find */}
            {canZoom && (
              <div
                data-zoom-toolbar
                className="pointer-events-auto absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-full border border-white/20 bg-black/75 px-2 py-1 shadow-lg backdrop-blur-sm"
                onPointerDown={(e) => e.stopPropagation()}
              >
                {ZoomControls}
              </div>
            )}
          </div>

          {(item?.width || item?.height || item?.size_bytes || item?.mime_type) && (
            <div className="flex items-center gap-4 border-t border-white/10 bg-black/70 px-4 py-1.5 text-xs text-white/60">
              {item.width && item.height ? (
                <span>{item.width} × {item.height}px</span>
              ) : null}
              {item.mime_type ? <span>{item.mime_type}</span> : null}
              {item.size_bytes ? <span>{formatBytes(item.size_bytes)}</span> : null}
              <span className="ml-auto hidden sm:inline">Scroll or ± to zoom · Drag to pan · 1 for 1:1</span>
            </div>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
