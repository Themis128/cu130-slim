'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Bell, Moon, Sun, Search, Command, Settings, Map, CheckCircle2, AlertCircle, AlertTriangle, Info, X } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/Avatar'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from '@/components/ui/DropdownMenu'
import { Separator } from '@/components/ui/Separator'
import { CommandPalette } from '@/components/ui/CommandPalette'
import { useAuth } from '@/hooks/useAuth'
import { useTheme } from '@/hooks/useTheme'
import { useNotifications } from '@/hooks/useNotifications'
import type { AppNotification } from '@/hooks/useNotifications'
import { cn } from '@/lib/utils'
import { useTour } from '@/hooks/useTour'

function NotifIcon({ type }: { type: AppNotification['type'] }) {
  if (type === 'success') return <CheckCircle2 className="h-4 w-4 flex-shrink-0 text-green-500" />
  if (type === 'error')   return <AlertCircle   className="h-4 w-4 flex-shrink-0 text-destructive" />
  if (type === 'warning') return <AlertTriangle  className="h-4 w-4 flex-shrink-0 text-amber-500" />
  return <Info className="h-4 w-4 flex-shrink-0 text-blue-500" />
}

export function Header() {
  const router = useRouter()
  const { user, logout } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const { start: startTour } = useTour()
  const [paletteOpen, setPaletteOpen] = useState(false)

  const { notifications, unreadCount, markAllRead, markRead, dismiss } = useNotifications()

  // Global ⌘K / Ctrl+K shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  return (
    <header className="sticky top-0 z-30 h-16 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 border-b">
      <div className="flex h-full items-center justify-between px-4 gap-4">
        {/* Mobile search button */}
        <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setPaletteOpen(true)}>
          <Search className="h-5 w-5" />
        </Button>

        {/* Command palette trigger */}
        <button
          onClick={() => setPaletteOpen(true)}
          className="flex-1 max-w-md hidden md:flex items-center gap-2 h-9 rounded-md border bg-muted/50 px-3 text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
        >
          <Search className="h-4 w-4 flex-shrink-0" />
          <span className="flex-1 text-left">Search pages, actions...</span>
          <kbd className="inline-flex items-center gap-0.5 rounded border bg-background px-1.5 py-0.5 text-[10px] font-medium">
            <Command className="h-2.5 w-2.5" />K
          </kbd>
        </button>

        <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />

        {/* Right side actions */}
        <div className="flex items-center gap-2">
          {/* Theme toggle */}
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {theme === 'dark' ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
          </Button>

          {/* Notifications */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="relative h-10 w-10" aria-label="Notifications">
                <Bell className="h-5 w-5" />
                {unreadCount > 0 && (
                  <span className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-destructive text-xs text-destructive-foreground font-medium">
                    {unreadCount > 9 ? '9+' : unreadCount}
                  </span>
                )}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-80">
              <div className="flex items-center justify-between px-3 py-2">
                <h4 className="font-semibold text-sm">Notifications</h4>
                {unreadCount > 0 && (
                  <Button variant="ghost" size="sm" className="text-xs h-7 px-2" onClick={markAllRead}>
                    Mark all read
                  </Button>
                )}
              </div>
              <Separator />
              <div className="max-h-96 overflow-y-auto">
                {notifications.length === 0 ? (
                  <div className="py-10 text-center text-sm text-muted-foreground">
                    <Bell className="h-8 w-8 mx-auto mb-2 opacity-30" />
                    All caught up
                  </div>
                ) : (
                  notifications.map((n) => (
                    <DropdownMenuItem
                      key={n.id}
                      className={cn(
                        'flex items-start gap-3 px-3 py-2.5 cursor-pointer focus:bg-accent group',
                        !n.read && 'bg-accent/30'
                      )}
                      onClick={() => {
                        markRead(n.id)
                        if (n.href) router.push(n.href)
                      }}
                    >
                      <NotifIcon type={n.type} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-2">
                          <p className="font-medium text-sm leading-snug">{n.title}</p>
                          <span className="text-[11px] text-muted-foreground whitespace-nowrap shrink-0">{n.time}</span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{n.message}</p>
                      </div>
                      <button
                        type="button"
                        aria-label="Dismiss notification"
                        className="shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 rounded p-0.5 hover:bg-muted text-muted-foreground hover:text-foreground transition-opacity"
                        onClick={(e) => {
                          e.stopPropagation()
                          dismiss(n.id)
                        }}
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </DropdownMenuItem>
                  ))
                )}
              </div>
              <Separator />
              <DropdownMenuItem asChild className="justify-center text-primary text-sm font-medium">
                <Link href="/content">View all activity</Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          {/* User menu */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" className="relative h-10 w-10 rounded-full">
                <Avatar className="h-10 w-10">
                  <AvatarImage src={user?.avatar_url || undefined} alt={user?.name || user?.email} />
                  <AvatarFallback className="text-xs">
                    {user?.name?.[0] || user?.email?.[0]?.toUpperCase() || 'U'}
                  </AvatarFallback>
                </Avatar>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <div className="px-2 py-2">
                <p className="font-medium text-sm">{user?.name || user?.email}</p>
                <p className="text-xs text-muted-foreground truncate">{user?.email}</p>
              </div>
              <Separator />
              <DropdownMenuItem asChild>
                <Link href="/settings">
                  <Settings className="mr-2 h-4 w-4" />
                  Settings
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem onClick={startTour}>
                <Map className="mr-2 h-4 w-4" />
                Take Tour
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem inset onClick={() => logout()}>
                <Command className="mr-2 h-4 w-4" />
                Logout
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  )
}
