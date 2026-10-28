'use client'

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'

export interface TourStep {
  id: string
  target: string
  title: string
  body: string
  page: string
  placement: 'top' | 'bottom' | 'left' | 'right'
  navigateTo?: string
}

export const TOUR_STEPS: TourStep[] = [
  {
    id: 'nav',
    target: 'nav',
    title: 'Navigate the app',
    body: 'Use this sidebar to jump between Dashboard, Content, Media, Analytics, Accounts, and Settings.',
    page: '/dashboard',
    placement: 'right',
  },
  {
    id: 'create-post',
    target: 'create-post',
    title: 'Create your first post',
    body: 'Click here to write content and publish across all your connected social platforms at once.',
    page: '/dashboard',
    placement: 'bottom',
  },
  {
    id: 'stats',
    target: 'stats',
    title: 'Your metrics at a glance',
    body: 'Track total posts, published, scheduled, and connected accounts — updated in real time.',
    page: '/dashboard',
    placement: 'bottom',
  },
  {
    id: 'top-posts',
    target: 'top-posts',
    title: 'Top performing posts',
    body: 'Your best content ranked by engagement. Use this to understand what resonates with your audience.',
    page: '/dashboard',
    placement: 'top',
  },
  {
    id: 'quick-actions',
    target: 'quick-actions',
    title: 'Quick shortcuts',
    body: "Jump to common tasks instantly. Next, let's create a post together.",
    page: '/dashboard',
    placement: 'left',
    navigateTo: '/content/new',
  },
  {
    id: 'platform-selector',
    target: 'platform-selector',
    title: 'Choose platforms',
    body: 'Select which social accounts to publish to. Connect more in Settings → Accounts.',
    page: '/content/new',
    placement: 'bottom',
  },
  {
    id: 'content-editor',
    target: 'content-editor',
    title: 'Write your content',
    body: 'The character counter adapts per platform — 280 for Twitter/X, 3 000 for LinkedIn, 2 200 for Instagram.',
    page: '/content/new',
    placement: 'bottom',
  },
  {
    id: 'ai-assist',
    target: 'ai-assist',
    title: 'AI-powered writing',
    body: 'Let Ollama generate a caption, suggest hashtags, or rewrite your draft — all running locally on your GPU.',
    page: '/content/new',
    placement: 'bottom',
  },
  {
    id: 'publish-actions',
    target: 'publish-actions',
    title: 'Publish or schedule',
    body: "Save as draft, pick a future date and time, or hit Publish Now. You're all set!",
    page: '/content/new',
    placement: 'top',
  },
]

interface TourContextValue {
  steps: TourStep[]
  currentStep: number
  isOpen: boolean
  start: () => void
  next: () => void
  prev: () => void
  skip: () => void
}

const TourContext = createContext<TourContextValue | null>(null)

export function TourProvider({ children }: { children: ReactNode }) {
  const [currentStep, setCurrentStep] = useState(0)
  const [isOpen, setIsOpen] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined') return
    const completed = localStorage.getItem('tour_completed')
    if (!completed) {
      const t = setTimeout(() => setIsOpen(true), 900)
      return () => clearTimeout(t)
    }
  }, [])

  const start = useCallback(() => {
    setCurrentStep(0)
    setIsOpen(true)
  }, [])

  const next = useCallback(() => {
    setCurrentStep(s => {
      if (s >= TOUR_STEPS.length - 1) {
        setIsOpen(false)
        localStorage.setItem('tour_completed', 'true')
        return s
      }
      return s + 1
    })
  }, [])

  const prev = useCallback(() => {
    setCurrentStep(s => Math.max(0, s - 1))
  }, [])

  const skip = useCallback(() => {
    setIsOpen(false)
    localStorage.setItem('tour_completed', 'true')
  }, [])

  return (
    <TourContext.Provider value={{ steps: TOUR_STEPS, currentStep, isOpen, start, next, prev, skip }}>
      {children}
    </TourContext.Provider>
  )
}

const NOOP_TOUR: TourContextValue = {
  steps: TOUR_STEPS,
  currentStep: 0,
  isOpen: false,
  start: () => {},
  next: () => {},
  prev: () => {},
  skip: () => {},
}

export function useTour() {
  return useContext(TourContext) ?? NOOP_TOUR
}
