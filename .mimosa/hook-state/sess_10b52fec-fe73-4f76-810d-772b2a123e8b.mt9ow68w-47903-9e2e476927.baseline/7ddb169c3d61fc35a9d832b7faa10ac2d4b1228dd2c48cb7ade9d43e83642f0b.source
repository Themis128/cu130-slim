'use client'

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  ReactNode,
} from 'react'
import { usePathname } from 'next/navigation'

export interface AdvisorSuggestion {
  id: string
  icon: string
  title: string
  body: string
  action?: { label: string; href: string }
}

export interface AdvisorCtx {
  content?: string
  selectedPlatforms?: string[]
  hasMedia?: boolean
  aiUsed?: boolean
  connectedAccountsCount?: number
  publishedCount?: number
  scheduledCount?: number
}

interface AdvisorContextValue {
  suggestion: AdvisorSuggestion | null
  setCtx: (patch: Partial<AdvisorCtx>) => void
  dismiss: () => void
}

const AdvisorContext = createContext<AdvisorContextValue | null>(null)

type Rule = (ctx: AdvisorCtx, page: string) => AdvisorSuggestion | null

const RULES: Rule[] = [
  // No accounts connected
  (ctx, page) =>
    page === '/dashboard' && ctx.connectedAccountsCount === 0
      ? {
          id: 'no-accounts',
          icon: '🔗',
          title: 'Connect your first account',
          body: 'Link a social account to start publishing. It only takes 30 seconds.',
          action: { label: 'Connect now', href: '/accounts' },
        }
      : null,

  // Has published posts but nothing scheduled
  (ctx, page) =>
    page === '/dashboard' &&
    (ctx.publishedCount ?? 0) > 0 &&
    (ctx.scheduledCount ?? 0) === 0
      ? {
          id: 'no-schedule',
          icon: '📅',
          title: 'Build a posting schedule',
          body: 'Consistent posting drives 3× more engagement. Schedule your next batch.',
          action: { label: 'Schedule posts', href: '/content' },
        }
      : null,

  // Content written but no platform selected
  (ctx, page) =>
    page.startsWith('/content/new') &&
    (ctx.selectedPlatforms?.length ?? 0) === 0 &&
    (ctx.content?.length ?? 0) > 20
      ? {
          id: 'no-platforms',
          icon: '📱',
          title: 'Pick a platform',
          body: "You've got content but no destination. Select which accounts to post to.",
        }
      : null,

  // Approaching Twitter char limit
  (ctx, page) => {
    if (!page.startsWith('/content/new')) return null
    if (!ctx.selectedPlatforms?.includes('twitter')) return null
    const len = ctx.content?.length ?? 0
    if (len >= 240 && len <= 280)
      return {
        id: 'twitter-limit',
        icon: '✂️',
        title: "Near Twitter's 280-char limit",
        body: `You're at ${len} characters. Consider splitting into a thread for more room.`,
      }
    return null
  },

  // No hashtags in a reasonably long post
  (ctx, page) => {
    if (!page.startsWith('/content/new')) return null
    const content = ctx.content ?? ''
    if (content.length > 60 && !content.includes('#'))
      return {
        id: 'no-hashtags',
        icon: '#️⃣',
        title: 'Boost reach with hashtags',
        body: 'Posts with hashtags get up to 30% more discovery. Click Hashtags for AI suggestions.',
      }
    return null
  },

  // No media attached to a post with content
  (ctx, page) => {
    if (!page.startsWith('/content/new')) return null
    const content = ctx.content ?? ''
    if (content.length > 30 && !ctx.hasMedia)
      return {
        id: 'add-media',
        icon: '🖼️',
        title: 'Posts with images get 3× more clicks',
        body: 'Add a photo or generate an AI image to make your post stand out.',
      }
    return null
  },

  // Long content but AI hasn't been used yet
  (ctx, page) => {
    if (!page.startsWith('/content/new')) return null
    const content = ctx.content ?? ''
    if (content.length > 50 && !ctx.aiUsed)
      return {
        id: 'try-ai',
        icon: '✨',
        title: 'Let AI improve this',
        body: 'Click "AI Assist" to enhance your draft, suggest hashtags, or shift the tone.',
      }
    return null
  },
]

const DEBOUNCE_MS = 1800
const SESSION_KEY = 'advisor_dismissed'

function getDismissed(): Set<string> {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    return new Set(raw ? JSON.parse(raw) : [])
  } catch {
    return new Set()
  }
}

function saveDismissed(ids: Set<string>) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(Array.from(ids)))
  } catch {}
}

export function AdvisorProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const [ctx, setCtxState] = useState<AdvisorCtx>({})
  const [suggestion, setSuggestion] = useState<AdvisorSuggestion | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const setCtx = useCallback((patch: Partial<AdvisorCtx>) => {
    setCtxState(prev => ({ ...prev, ...patch }))
  }, [])

  // Reset editor context when leaving the editor
  useEffect(() => {
    if (!(pathname ?? '').startsWith('/content/new')) {
      setCtxState(prev => ({
        connectedAccountsCount: prev.connectedAccountsCount,
        publishedCount: prev.publishedCount,
        scheduledCount: prev.scheduledCount,
      }))
    }
  }, [pathname])

  // Run rule engine with debounce
  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      const dismissed = getDismissed()
      const page = pathname ?? ''
      for (const rule of RULES) {
        const s = rule(ctx, page)
        if (s && !dismissed.has(s.id)) {
          setSuggestion(s)
          return
        }
      }
      setSuggestion(null)
    }, DEBOUNCE_MS)

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [ctx, pathname])

  const dismiss = useCallback(() => {
    if (!suggestion) return
    const dismissed = getDismissed()
    dismissed.add(suggestion.id)
    saveDismissed(dismissed)
    setSuggestion(null)
  }, [suggestion])

  return (
    <AdvisorContext.Provider value={{ suggestion, setCtx, dismiss }}>
      {children}
    </AdvisorContext.Provider>
  )
}

export function useAdvisor() {
  const ctx = useContext(AdvisorContext)
  if (!ctx) throw new Error('useAdvisor must be inside AdvisorProvider')
  return ctx
}
