'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import {
  LayoutDashboard, FileText, Image, Zap, Users, BarChart3, Settings,
  Search, Plus, Upload, Cpu, LogOut, Moon, Sun, ArrowRight,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuth } from '@/hooks/useAuth'
import { useTheme } from '@/hooks/useTheme'

interface CommandItem {
  id: string
  label: string
  group: string
  icon: React.ElementType
  action: () => void
  keywords?: string[]
}

interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useRouter()
  const { logout } = useAuth()
  const { toggleTheme, theme } = useTheme()
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const navigate = useCallback(
    (path: string) => {
      onOpenChange(false)
      router.push(path)
    },
    [onOpenChange, router]
  )

  const commands: CommandItem[] = [
    // Navigation
    { id: 'nav-dashboard',  label: 'Dashboard',      group: 'Navigation', icon: LayoutDashboard, action: () => navigate('/dashboard') },
    { id: 'nav-content',    label: 'New Post',        group: 'Navigation', icon: FileText,        action: () => navigate('/content/new') },
    { id: 'nav-media',      label: 'Media Library',   group: 'Navigation', icon: Image,           action: () => navigate('/media') },
    { id: 'nav-workflows',  label: 'Workflows',       group: 'Navigation', icon: Zap,             action: () => navigate('/workflows') },
    { id: 'nav-accounts',   label: 'Accounts',        group: 'Navigation', icon: Users,           action: () => navigate('/accounts') },
    { id: 'nav-analytics',  label: 'Analytics',       group: 'Navigation', icon: BarChart3,       action: () => navigate('/analytics') },
    { id: 'nav-settings',   label: 'Settings',        group: 'Navigation', icon: Settings,        action: () => navigate('/settings') },
    { id: 'nav-ai',         label: 'AI Providers',    group: 'Navigation', icon: Cpu,             action: () => navigate('/settings/ai-providers') },
    // Quick Actions
    { id: 'action-new-post',    label: 'Create new post',       group: 'Actions', icon: Plus,   action: () => navigate('/content/new'),        keywords: ['write', 'compose', 'draft'] },
    { id: 'action-carousel',    label: 'Create carousel',       group: 'Actions', icon: Plus,   action: () => navigate('/content/carousel/new'), keywords: ['slides', 'carousel'] },
    { id: 'action-upload',      label: 'Upload media',          group: 'Actions', icon: Upload, action: () => navigate('/media'),              keywords: ['image', 'video', 'file'] },
    { id: 'action-workflow',    label: 'Generate workflow',     group: 'Actions', icon: Zap,    action: () => navigate('/workflows'),           keywords: ['automation', 'n8n'] },
    { id: 'action-connect',     label: 'Connect social account',group: 'Actions', icon: Users,  action: () => navigate('/accounts'),           keywords: ['linkedin', 'twitter', 'instagram'] },
    // App
    { id: 'app-theme',   label: theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode', group: 'App', icon: theme === 'dark' ? Sun : Moon, action: () => { toggleTheme(); onOpenChange(false) }, keywords: ['theme', 'dark', 'light'] },
    { id: 'app-logout',  label: 'Log out',  group: 'App', icon: LogOut, action: () => { onOpenChange(false); logout() }, keywords: ['sign out'] },
  ]

  const filtered = query.trim()
    ? commands.filter((cmd) => {
        const q = query.toLowerCase()
        return (
          cmd.label.toLowerCase().includes(q) ||
          cmd.group.toLowerCase().includes(q) ||
          cmd.keywords?.some((k) => k.includes(q))
        )
      })
    : commands

  const grouped = filtered.reduce<Record<string, CommandItem[]>>((acc, cmd) => {
    acc[cmd.group] = acc[cmd.group] ? [...acc[cmd.group], cmd] : [cmd]
    return acc
  }, {})

  // Reset state when opening
  useEffect(() => {
    if (open) {
      setQuery('')
      setActiveIndex(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  // Keep activeIndex in bounds when filter changes
  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      filtered[activeIndex]?.action()
    }
  }

  let flatIndex = 0

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          className={cn(
            'fixed left-[50%] top-[20%] z-50 w-full max-w-xl translate-x-[-50%] rounded-xl border bg-background shadow-2xl',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
            'data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95',
            'data-[state=closed]:slide-out-to-left-1/2 data-[state=open]:slide-in-from-left-1/2',
            'data-[state=closed]:slide-out-to-top-[8%] data-[state=open]:slide-in-from-top-[8%]'
          )}
          onKeyDown={handleKeyDown}
          aria-label="Command palette"
        >
          <DialogPrimitive.Title className="sr-only">Command palette</DialogPrimitive.Title>

          {/* Search input */}
          <div className="flex items-center gap-3 border-b px-4 py-3">
            <Search className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
            <input
              ref={inputRef}
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              placeholder="Search pages, actions..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <kbd className="hidden sm:inline-flex items-center rounded border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              esc
            </kbd>
          </div>

          {/* Results */}
          <div className="max-h-[360px] overflow-y-auto p-2">
            {filtered.length === 0 ? (
              <div className="py-10 text-center text-sm text-muted-foreground">
                No results for &ldquo;{query}&rdquo;
              </div>
            ) : (
              Object.entries(grouped).map(([group, items]) => (
                <div key={group} className="mb-1">
                  <p className="px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {group}
                  </p>
                  {items.map((item) => {
                    const idx = flatIndex++
                    const isActive = idx === activeIndex
                    return (
                      <button
                        key={item.id}
                        className={cn(
                          'flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-left transition-colors',
                          isActive
                            ? 'bg-primary text-primary-foreground'
                            : 'hover:bg-accent hover:text-accent-foreground'
                        )}
                        onMouseEnter={() => setActiveIndex(idx)}
                        onClick={item.action}
                      >
                        <item.icon className="h-4 w-4 flex-shrink-0" />
                        <span className="flex-1">{item.label}</span>
                        {isActive && <ArrowRight className="h-3.5 w-3.5 opacity-60" />}
                      </button>
                    )
                  })}
                </div>
              ))
            )}
          </div>

          {/* Footer hint */}
          <div className="flex items-center gap-3 border-t px-4 py-2 text-[11px] text-muted-foreground">
            <span><kbd className="rounded border bg-muted px-1 py-0.5">↑↓</kbd> navigate</span>
            <span><kbd className="rounded border bg-muted px-1 py-0.5">↵</kbd> select</span>
            <span><kbd className="rounded border bg-muted px-1 py-0.5">esc</kbd> close</span>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
