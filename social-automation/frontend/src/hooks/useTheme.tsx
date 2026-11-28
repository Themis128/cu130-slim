'use client'

import { createContext, useContext, useEffect, useState, ReactNode } from 'react'

type Theme = 'light' | 'dark' | 'system'

interface ThemeContextType {
  theme: Theme
  resolvedTheme: 'light' | 'dark'
  toggleTheme: () => void
  setTheme: (theme: Theme) => void
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined)

const resolveFromMedia = (t: Theme): 'light' | 'dark' => {
  if (t !== 'system') return t
  if (typeof window !== 'undefined') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  return 'light'
}

// Keep <meta name="color-scheme"> in sync with the stored theme override
// ("light dark" follows the system, "light"/"dark" pin a scheme).
const syncColorSchemeMeta = (t: Theme) => {
  const meta = document.querySelector('meta[name="color-scheme"]')
  if (meta) {
    meta.setAttribute('content', t === 'system' ? 'light dark' : t)
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>('dark')
  const [resolvedTheme, setResolvedTheme] = useState<'light' | 'dark'>('dark')
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
    const stored = localStorage.getItem('theme') as Theme | null
    // Brand-first: new visitors land on the Cloudless dark theme.
    const initialTheme = stored || 'dark'
    const resolved = resolveFromMedia(initialTheme)
    setThemeState(initialTheme)
    setResolvedTheme(resolved)
    document.documentElement.classList.toggle('dark', resolved === 'dark')
    syncColorSchemeMeta(initialTheme)
  }, [])

  useEffect(() => {
    if (!mounted) return
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const handleChange = (e: MediaQueryListEvent) => {
      if (theme === 'system') {
        const resolved = e.matches ? 'dark' : 'light'
        setResolvedTheme(resolved)
        document.documentElement.classList.toggle('dark', resolved === 'dark')
        syncColorSchemeMeta('system')
      }
    }
    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [theme, mounted])

  const setTheme = (newTheme: Theme) => {
    setThemeState(newTheme)
    localStorage.setItem('theme', newTheme)
    const resolved = resolveFromMedia(newTheme)
    setResolvedTheme(resolved)
    document.documentElement.classList.toggle('dark', resolved === 'dark')
    syncColorSchemeMeta(newTheme)
  }

  const toggleTheme = () => {
    setTheme(resolvedTheme === 'light' ? 'dark' : 'light')
  }

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, toggleTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider')
  }
  return context
}
