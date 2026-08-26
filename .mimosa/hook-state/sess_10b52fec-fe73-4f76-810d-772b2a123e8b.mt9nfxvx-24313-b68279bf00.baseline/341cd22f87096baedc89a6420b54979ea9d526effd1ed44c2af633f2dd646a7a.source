'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useRouter, usePathname } from 'next/navigation'
import { useTour, TOUR_STEPS } from '@/hooks/useTour'
import { Button } from '@/components/ui/Button'
import { X, ArrowLeft, ArrowRight, Map } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Rect { top: number; left: number; width: number; height: number }

const PAD = 6

function getTargetRect(target: string): Rect | null {
  const el = document.querySelector(`[data-tour="${target}"]`)
  if (!el) return null
  const r = el.getBoundingClientRect()
  return {
    top: r.top - PAD,
    left: r.left - PAD,
    width: r.width + PAD * 2,
    height: r.height + PAD * 2,
  }
}

function getTooltipStyle(
  rect: Rect,
  placement: 'top' | 'bottom' | 'left' | 'right'
): React.CSSProperties {
  const GAP = 14
  const W = 300

  switch (placement) {
    case 'bottom':
      return {
        position: 'fixed',
        top: rect.top + rect.height + GAP,
        left: Math.max(12, Math.min(rect.left, window.innerWidth - W - 12)),
        width: W,
      }
    case 'top':
      return {
        position: 'fixed',
        bottom: window.innerHeight - rect.top + GAP,
        left: Math.max(12, Math.min(rect.left, window.innerWidth - W - 12)),
        width: W,
      }
    case 'right':
      return {
        position: 'fixed',
        top: rect.top,
        left: rect.left + rect.width + GAP,
        width: W,
      }
    case 'left':
      return {
        position: 'fixed',
        top: rect.top,
        right: window.innerWidth - rect.left + GAP,
        width: W,
      }
  }
}

export function TourOverlay() {
  const { steps, currentStep, isOpen, next, prev, skip } = useTour()
  const pathname = usePathname()
  const router = useRouter()
  const [rect, setRect] = useState<Rect | null>(null)
  const [mounted, setMounted] = useState(false)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const step = steps[currentStep]
  const onCurrentPage = step?.page === pathname

  // Find the target element (retry if not yet rendered)
  const findTarget = useCallback(() => {
    if (!step || !onCurrentPage) return
    const r = getTargetRect(step.target)
    if (r) {
      setRect(r)
    } else {
      retryRef.current = setTimeout(findTarget, 120)
    }
  }, [step, onCurrentPage])

  useEffect(() => {
    setMounted(true)
    return () => setMounted(false)
  }, [])

  useEffect(() => {
    if (!isOpen) { setRect(null); return }
    if (retryRef.current) clearTimeout(retryRef.current)
    setRect(null)
    findTarget()
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current)
    }
  }, [isOpen, currentStep, pathname, findTarget])

  // Recompute on scroll/resize
  useEffect(() => {
    if (!isOpen || !onCurrentPage) return
    const update = () => {
      const r = step ? getTargetRect(step.target) : null
      if (r) setRect(r)
    }
    window.addEventListener('scroll', update, { passive: true })
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update)
      window.removeEventListener('resize', update)
    }
  }, [isOpen, currentStep, onCurrentPage, step])

  // Navigate to next page when needed
  const handleNext = useCallback(() => {
    if (step?.navigateTo) {
      router.push(step.navigateTo)
    }
    next()
  }, [step, next, router])

  if (!mounted || !isOpen || !step) return null

  // If we're not on the right page, show a minimal "getting there..." indicator
  if (!onCurrentPage) {
    return createPortal(
      <div className="fixed bottom-6 right-6 z-[9999] bg-popover border border-border rounded-xl shadow-xl px-4 py-3 flex items-center gap-3 text-sm">
        <Map className="h-4 w-4 text-primary animate-pulse" />
        <span className="text-muted-foreground">Navigating to the next step…</span>
      </div>,
      document.body
    )
  }

  const tooltipStyle = rect ? getTooltipStyle(rect, step.placement) : undefined

  return createPortal(
    <>
      {/* Backdrop — click to skip */}
      <div
        className="fixed inset-0 z-[9990]"
        style={{ cursor: 'default' }}
        aria-hidden="true"
      />

      {/* Spotlight box */}
      {rect && (
        <div
          className="fixed z-[9991] pointer-events-none rounded-lg transition-all duration-200"
          style={{
            top: rect.top,
            left: rect.left,
            width: rect.width,
            height: rect.height,
            boxShadow: '0 0 0 2px hsl(var(--primary)), 0 0 0 9999px rgba(0,0,0,0.65)',
          }}
        />
      )}

      {/* Tooltip card */}
      {tooltipStyle && (
        <div
          className="fixed z-[9992] bg-popover border border-border rounded-xl shadow-2xl p-4 animate-in fade-in-0 slide-in-from-bottom-2 duration-200"
          style={tooltipStyle}
          role="dialog"
          aria-label={`Tour step ${currentStep + 1}: ${step.title}`}
        >
          {/* Header */}
          <div className="flex items-start justify-between gap-2 mb-1">
            <div className="flex items-center gap-2">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground text-[10px] font-bold flex-shrink-0">
                {currentStep + 1}
              </span>
              <h3 className="font-semibold text-sm leading-tight">{step.title}</h3>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6 text-muted-foreground hover:text-foreground -mt-0.5 -mr-1 flex-shrink-0"
              onClick={skip}
              aria-label="Skip tour"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>

          <p className="text-sm text-muted-foreground leading-relaxed mb-3">{step.body}</p>

          {/* Progress dots */}
          <div className="flex items-center gap-1 mb-3">
            {steps.map((_, i) => (
              <div
                key={i}
                className={cn(
                  'h-1.5 rounded-full transition-all duration-200',
                  i === currentStep ? 'w-4 bg-primary' : 'w-1.5 bg-border'
                )}
              />
            ))}
          </div>

          {/* Navigation */}
          <div className="flex items-center justify-between">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs text-muted-foreground"
              onClick={skip}
            >
              Skip tour
            </Button>
            <div className="flex items-center gap-1.5">
              {currentStep > 0 && (
                <Button variant="outline" size="sm" className="h-7 px-2" onClick={prev}>
                  <ArrowLeft className="h-3 w-3" />
                </Button>
              )}
              <Button
                size="sm"
                className="h-7 px-3 text-xs"
                onClick={handleNext}
              >
                {currentStep === steps.length - 1
                  ? 'Done'
                  : step.navigateTo
                  ? 'Next →'
                  : (
                    <>
                      Next
                      <ArrowRight className="ml-1 h-3 w-3" />
                    </>
                  )}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>,
    document.body
  )
}
