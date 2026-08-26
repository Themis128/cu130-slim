'use client'

import { useState, useRef, useCallback } from 'react'
import {
  startOfMonth, endOfMonth, eachDayOfInterval, getDay, format,
  addMonths, subMonths, isSameDay, isToday, addDays,
} from 'date-fns'
import { ChevronLeft, ChevronRight, Plus, Clock } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { useScheduledPosts, useSchedulePost } from '@/hooks/useQueries'
import { cn } from '@/lib/utils'
import Link from 'next/link'
import type { Post, PostTarget, SocialAccount } from '@/types'
import toast from 'react-hot-toast'

const PLATFORM_COLORS: Record<string, string> = {
  linkedin:  'bg-blue-600',
  twitter:   'bg-sky-500',
  instagram: 'bg-pink-500',
  facebook:  'bg-blue-700',
  threads:   'bg-gray-700',
}

const PLATFORM_TEXT: Record<string, string> = {
  linkedin:  'text-blue-600',
  twitter:   'text-sky-500',
  instagram: 'text-pink-500',
  facebook:  'text-blue-700',
  threads:   'text-gray-600',
}

function getPlatforms(post: Post): string[] {
  const via = post.targets
    ?.map((t: PostTarget) => (t.social_account as SocialAccount | undefined)?.platform)
    .filter(Boolean) as string[] | undefined
  return via?.length ? Array.from(new Set(via)) : []
}

// Monday-first week
const WEEK_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function mondayFirst(date: Date): number {
  return (getDay(date) + 6) % 7  // 0=Mon…6=Sun
}

export default function CalendarPage() {
  const [month, setMonth] = useState(() => {
    const d = new Date()
    d.setDate(1)
    d.setHours(0, 0, 0, 0)
    return d
  })
  const { data: posts = [], refetch } = useScheduledPosts()
  const schedulePost = useSchedulePost()

  // Optimistic local overrides: postId → new scheduled_at
  const [localOverrides, setLocalOverrides] = useState<Record<string, string>>({})

  const draggingId = useRef<string | null>(null)

  const effectivePosts: Post[] = posts.map((p: Post) =>
    localOverrides[p.id] ? { ...p, scheduled_at: localOverrides[p.id] } : p
  )

  const days = eachDayOfInterval({ start: startOfMonth(month), end: endOfMonth(month) })
  const startPad = mondayFirst(days[0])  // empty cells before first day

  const postsForDay = useCallback(
    (day: Date) => effectivePosts.filter(p => p.scheduled_at && isSameDay(new Date(p.scheduled_at), day)),
    [effectivePosts]
  )

  // Drag handlers
  const onDragStart = (e: React.DragEvent, postId: string) => {
    draggingId.current = postId
    e.dataTransfer.effectAllowed = 'move'
  }

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }

  const onDrop = async (e: React.DragEvent, targetDay: Date) => {
    e.preventDefault()
    const id = draggingId.current
    if (!id) return
    draggingId.current = null

    const post = effectivePosts.find(p => p.id === id)
    if (!post?.scheduled_at) return

    // Keep the original time, just change the date
    const originalDate = new Date(post.scheduled_at)
    const newDate = new Date(targetDay)
    newDate.setHours(originalDate.getHours(), originalDate.getMinutes(), 0, 0)

    // Skip if same day
    if (isSameDay(originalDate, newDate)) return

    const newIso = newDate.toISOString()

    // Optimistic update
    setLocalOverrides(prev => ({ ...prev, [id]: newIso }))

    try {
      await schedulePost.mutateAsync({ id, scheduled_at: newIso })
      toast.success(`Moved to ${format(newDate, 'MMM d')}`)
      refetch()
    } catch {
      // Rollback
      setLocalOverrides(prev => {
        const next = { ...prev }
        delete next[id]
        return next
      })
      toast.error('Failed to reschedule post')
    }
  }

  const [selectedDay, setSelectedDay] = useState<Date | null>(null)
  const selectedDayPosts = selectedDay ? postsForDay(selectedDay) : []

  const totalThisMonth = effectivePosts.filter(p =>
    p.scheduled_at &&
    new Date(p.scheduled_at) >= startOfMonth(month) &&
    new Date(p.scheduled_at) <= endOfMonth(month)
  ).length

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Calendar</h1>
          <p className="text-muted-foreground mt-1">
            {totalThisMonth} scheduled post{totalThisMonth !== 1 ? 's' : ''} in {format(month, 'MMMM yyyy')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="icon" onClick={() => setMonth(m => subMonths(m, 1))}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button variant="outline" className="min-w-[140px]" onClick={() => setMonth(new Date(new Date().getFullYear(), new Date().getMonth(), 1))}>
            {format(month, 'MMMM yyyy')}
          </Button>
          <Button variant="outline" size="icon" onClick={() => setMonth(m => addMonths(m, 1))}>
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button asChild>
            <Link href="/content/new">
              <Plus className="mr-2 h-4 w-4" />
              New Post
            </Link>
          </Button>
        </div>
      </div>

      {/* Calendar grid */}
      <div className="rounded-xl border bg-card overflow-hidden">
        {/* Day-of-week headers */}
        <div className="grid grid-cols-7 border-b bg-muted/40">
          {WEEK_DAYS.map(d => (
            <div key={d} className="py-2 text-center text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              {d}
            </div>
          ))}
        </div>

        {/* Day cells */}
        <div className="grid grid-cols-7">
          {/* Leading empty cells */}
          {Array.from({ length: startPad }).map((_, i) => (
            <div key={`pad-${i}`} className="min-h-[110px] border-r border-b bg-muted/10 last:border-r-0" />
          ))}

          {days.map((day, i) => {
            const dayPosts = postsForDay(day)
            const isSelected = selectedDay ? isSameDay(day, selectedDay) : false
            const today = isToday(day)
            const col = (startPad + i) % 7
            const isLastCol = col === 6

            return (
              <div
                key={day.toISOString()}
                onDragOver={onDragOver}
                onDrop={e => onDrop(e, day)}
                onClick={() => setSelectedDay(isSelected ? null : day)}
                className={cn(
                  'min-h-[110px] border-b p-1.5 cursor-pointer transition-colors group relative',
                  !isLastCol && 'border-r',
                  isSelected ? 'bg-primary/5 ring-1 ring-inset ring-primary/30' : 'hover:bg-muted/30',
                )}
              >
                {/* Day number */}
                <div className="flex items-center justify-between mb-1">
                  <span className={cn(
                    'flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold',
                    today ? 'bg-primary text-primary-foreground' : 'text-muted-foreground'
                  )}>
                    {format(day, 'd')}
                  </span>
                  {dayPosts.length > 0 && (
                    <span className="text-[9px] font-medium text-muted-foreground">
                      {dayPosts.length}
                    </span>
                  )}
                </div>

                {/* Post chips (show up to 3, then overflow) */}
                <div className="space-y-0.5">
                  {dayPosts.slice(0, 3).map(post => {
                    const platforms = getPlatforms(post)
                    const firstPlatform = platforms[0]
                    return (
                      <div
                        key={post.id}
                        draggable
                        onDragStart={e => { e.stopPropagation(); onDragStart(e, post.id) }}
                        onClick={e => e.stopPropagation()}
                        className={cn(
                          'flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium cursor-grab active:cursor-grabbing',
                          'bg-primary/10 text-foreground hover:bg-primary/20 transition-colors truncate',
                        )}
                        title={post.content_text || ''}
                      >
                        {firstPlatform && (
                          <span className={cn('w-1.5 h-1.5 rounded-full flex-shrink-0', PLATFORM_COLORS[firstPlatform] ?? 'bg-gray-400')} />
                        )}
                        <span className="truncate">{post.content_text?.slice(0, 28) ?? '—'}</span>
                      </div>
                    )
                  })}
                  {dayPosts.length > 3 && (
                    <div className="text-[10px] text-muted-foreground pl-1.5">
                      +{dayPosts.length - 3} more
                    </div>
                  )}
                </div>

                {/* Quick-add hover button */}
                <Link
                  href={`/content/new?date=${format(day, 'yyyy-MM-dd')}`}
                  onClick={e => e.stopPropagation()}
                  className="absolute bottom-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground hover:scale-110 transition-transform">
                    <Plus className="h-3 w-3" />
                  </span>
                </Link>
              </div>
            )
          })}

          {/* Trailing empty cells to fill last row */}
          {(() => {
            const total = startPad + days.length
            const remainder = total % 7
            const trailing = remainder === 0 ? 0 : 7 - remainder
            return Array.from({ length: trailing }).map((_, i) => (
              <div key={`trail-${i}`} className={cn('min-h-[110px] border-b bg-muted/10', i < trailing - 1 && 'border-r')} />
            ))
          })()}
        </div>
      </div>

      {/* Day detail panel */}
      {selectedDay && (
        <div className="rounded-xl border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">{format(selectedDay, 'EEEE, MMMM d')}</h2>
            <Button size="sm" asChild>
              <Link href={`/content/new?date=${format(selectedDay, 'yyyy-MM-dd')}`}>
                <Plus className="mr-1.5 h-3.5 w-3.5" />
                Schedule for this day
              </Link>
            </Button>
          </div>

          {selectedDayPosts.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">Nothing scheduled — click + to create a post.</p>
          ) : (
            <div className="space-y-2">
              {selectedDayPosts.map(post => {
                const platforms = getPlatforms(post)
                const time = post.scheduled_at ? format(new Date(post.scheduled_at), 'h:mm a') : ''
                return (
                  <Link
                    key={post.id}
                    href={`/content/${post.id}/edit`}
                    className="flex items-start gap-3 rounded-lg border bg-background px-3 py-3 hover:bg-accent transition-colors group"
                  >
                    {/* Platform dots */}
                    <div className="flex flex-col gap-1 pt-0.5">
                      {platforms.slice(0, 3).map(p => (
                        <span key={p} className={cn('h-2 w-2 rounded-full', PLATFORM_COLORS[p] ?? 'bg-gray-400')} title={p} />
                      ))}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-foreground truncate">
                        {post.content_text?.slice(0, 80) ?? 'No content'}
                        {(post.content_text?.length ?? 0) > 80 ? '…' : ''}
                      </p>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-xs text-muted-foreground flex items-center gap-1">
                          <Clock className="h-3 w-3" />{time}
                        </span>
                        {platforms.map(p => (
                          <Badge key={p} variant="outline" className={cn('text-[10px] px-1 py-0 capitalize', PLATFORM_TEXT[p])}>
                            {p}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  </Link>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-muted-foreground px-1">
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-primary" />Today</span>
        <span>Drag a post chip to a new date to reschedule it</span>
      </div>
    </div>
  )
}
