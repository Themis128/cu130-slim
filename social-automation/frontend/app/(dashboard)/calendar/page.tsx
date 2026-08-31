'use client'

import { useState, useRef, useCallback } from 'react'
import {
  startOfMonth, endOfMonth, eachDayOfInterval, getDay, format,
  addMonths, subMonths, isSameDay, isToday, isPast,
} from 'date-fns'
import {
  ChevronLeft, ChevronRight, Plus, Clock, CalendarDays,
  LayoutGrid, CheckCircle2, AlertCircle, FileText, Send,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { useScheduledPosts, useSchedulePost } from '@/hooks/useQueries'
import { cn, formatTime, isOnAthensCalendarDay, moveToAthensDay, athensDateKey } from '@/lib/utils'
import { WeekCalendar } from '@/components/ui/WeekCalendar'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { contentApi } from '@/services/api'
import type { Post, PostTarget, SocialAccount } from '@/types'
import toast from 'react-hot-toast'

// ─── constants ────────────────────────────────────────────────────────────────

const PLATFORMS = ['linkedin', 'twitter', 'instagram', 'facebook', 'threads'] as const
type Platform = typeof PLATFORMS[number]

const PLATFORM_COLORS: Record<string, string> = {
  linkedin:  'bg-blue-600',
  twitter:   'bg-sky-500',
  instagram: 'bg-pink-500',
  facebook:  'bg-blue-700',
  threads:   'bg-gray-600',
}

const PLATFORM_BORDER: Record<string, string> = {
  linkedin:  'border-blue-600/40',
  twitter:   'border-sky-500/40',
  instagram: 'border-pink-500/40',
  facebook:  'border-blue-700/40',
  threads:   'border-gray-600/40',
}

const PLATFORM_LABEL: Record<string, string> = {
  linkedin:  'LI',
  twitter:   'X',
  instagram: 'IG',
  facebook:  'FB',
  threads:   'TH',
}

type PostStatus = Post['status']

const STATUS_CHIP: Record<PostStatus, string> = {
  draft:      'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
  scheduled:  'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300',
  publishing: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
  published:  'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300',
  failed:     'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300',
  archived:   'bg-gray-50 text-gray-500 dark:bg-gray-900 dark:text-gray-500',
}

const STATUS_DOT: Record<PostStatus, string> = {
  draft:      'bg-gray-400',
  scheduled:  'bg-blue-500',
  publishing: 'bg-amber-500',
  published:  'bg-green-500',
  failed:     'bg-red-500',
  archived:   'bg-gray-300',
}

const STATUS_ICON: Record<PostStatus, React.ElementType> = {
  draft:      FileText,
  scheduled:  Clock,
  publishing: Send,
  published:  CheckCircle2,
  failed:     AlertCircle,
  archived:   FileText,
}

// Monday-first
const WEEK_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
function mondayFirst(date: Date) { return (getDay(date) + 6) % 7 }

function getPlatforms(post: Post): string[] {
  const via = post.targets
    ?.map((t: PostTarget) => (t.social_account as SocialAccount | undefined)?.platform)
    .filter(Boolean) as string[] | undefined
  return via?.length ? Array.from(new Set(via)) : []
}

// ─── hooks ────────────────────────────────────────────────────────────────────

function useAllCalendarPosts() {
  return useQuery({
    queryKey: ['posts', 'calendar-all'],
    queryFn: () => contentApi.listPosts({ page_size: 100 }),
    select: (response) => {
      const data = response.data
      const posts: Post[] = data?.posts ?? data?.items ?? (Array.isArray(data) ? data : [])
      return posts.filter(p => p.scheduled_at)
    },
    refetchInterval: 60_000,
  })
}

// ─── page ─────────────────────────────────────────────────────────────────────

type ViewMode = 'month' | 'week'

export default function CalendarPage() {
  const [month, setMonth] = useState(() => {
    const d = new Date(); d.setDate(1); d.setHours(0, 0, 0, 0); return d
  })
  const [view, setView] = useState<ViewMode>('month')
  const [platformFilter, setPlatformFilter] = useState<string | null>(null)
  const [selectedDay, setSelectedDay] = useState<Date | null>(null)

  const { data: allPosts = [], refetch } = useAllCalendarPosts()
  const { data: scheduledPosts = [] } = useScheduledPosts()
  const schedulePost = useSchedulePost()

  const [localOverrides, setLocalOverrides] = useState<Record<string, string>>({})
  const draggingId = useRef<string | null>(null)

  const effectivePosts: Post[] = allPosts.map((p: Post) =>
    localOverrides[p.id] ? { ...p, scheduled_at: localOverrides[p.id] } : p
  )

  const filteredPosts = platformFilter
    ? effectivePosts.filter(p => getPlatforms(p).includes(platformFilter))
    : effectivePosts

  const days = eachDayOfInterval({ start: startOfMonth(month), end: endOfMonth(month) })
  const startPad = mondayFirst(days[0])

  const postsForDay = useCallback(
    (day: Date) => filteredPosts.filter(p => p.scheduled_at && isOnAthensCalendarDay(p.scheduled_at, day)),
    [filteredPosts]
  )

  // ── drag & drop ─────────────────────────────────────────────────────────────

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
    if (isOnAthensCalendarDay(post.scheduled_at, targetDay)) return
    const newIso = moveToAthensDay(post.scheduled_at, targetDay)
    setLocalOverrides(prev => ({ ...prev, [id]: newIso }))
    try {
      await schedulePost.mutateAsync({ id, scheduled_at: newIso })
      toast.success(`Moved to ${format(targetDay, 'MMM d')}`)
      refetch()
    } catch {
      setLocalOverrides(prev => { const next = { ...prev }; delete next[id]; return next })
      toast.error('Failed to reschedule post')
    }
  }

  // ── stats ────────────────────────────────────────────────────────────────────

  const monthPrefix = `${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, '0')}`
  const monthPosts = effectivePosts.filter(p => p.scheduled_at && athensDateKey(p.scheduled_at).startsWith(monthPrefix))

  const stats = {
    total:     monthPosts.length,
    scheduled: monthPosts.filter(p => p.status === 'scheduled').length,
    published: monthPosts.filter(p => p.status === 'published').length,
    failed:    monthPosts.filter(p => p.status === 'failed').length,
    draft:     monthPosts.filter(p => p.status === 'draft').length,
  }

  const selectedDayPosts = selectedDay ? postsForDay(selectedDay) : []

  const isCurrentMonth =
    month.getFullYear() === new Date().getFullYear() &&
    month.getMonth() === new Date().getMonth()

  // ─────────────────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-5">

      {/* ── Header ── */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-3xl font-bold tracking-tight">Calendar</h1>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span>{stats.total} post{stats.total !== 1 ? 's' : ''} in {format(month, 'MMMM yyyy')}</span>
            {stats.scheduled > 0 && (
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-blue-500" />{stats.scheduled} scheduled
              </span>
            )}
            {stats.published > 0 && (
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-green-500" />{stats.published} published
              </span>
            )}
            {stats.failed > 0 && (
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-red-500" />{stats.failed} failed
              </span>
            )}
            {stats.draft > 0 && (
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-gray-400" />{stats.draft} draft
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* View toggle */}
          <div className="flex rounded-lg border overflow-hidden">
            <button
              onClick={() => setView('month')}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors',
                view === 'month' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted text-muted-foreground'
              )}
            >
              <LayoutGrid className="h-3.5 w-3.5" />Month
            </button>
            <button
              onClick={() => setView('week')}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors border-l',
                view === 'week' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted text-muted-foreground'
              )}
            >
              <CalendarDays className="h-3.5 w-3.5" />Week
            </button>
          </div>

          {/* Nav */}
          <Button variant="outline" size="icon" aria-label="Previous month" onClick={() => setMonth(m => subMonths(m, 1))}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant={isCurrentMonth ? 'default' : 'outline'}
            className="min-w-[140px] text-sm"
            onClick={() => setMonth(new Date(new Date().getFullYear(), new Date().getMonth(), 1))}
          >
            {format(month, 'MMMM yyyy')}
          </Button>
          <Button variant="outline" size="icon" aria-label="Next month" onClick={() => setMonth(m => addMonths(m, 1))}>
            <ChevronRight className="h-4 w-4" />
          </Button>

          <Button asChild>
            <Link href="/content/new">
              <Plus className="mr-1.5 h-4 w-4" />New Post
            </Link>
          </Button>
        </div>
      </div>

      {/* ── Platform filter ── */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => setPlatformFilter(null)}
          className={cn(
            'rounded-full px-3 py-1 text-xs font-medium border transition-colors',
            !platformFilter
              ? 'bg-foreground text-background border-foreground'
              : 'border-border text-muted-foreground hover:bg-muted'
          )}
        >
          All platforms
        </button>
        {PLATFORMS.map(p => {
          const active = platformFilter === p
          return (
            <button
              key={p}
              onClick={() => setPlatformFilter(active ? null : p)}
              className={cn(
                'flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium border transition-colors',
                active
                  ? cn('border-transparent text-white', PLATFORM_COLORS[p])
                  : cn('hover:bg-muted text-muted-foreground', PLATFORM_BORDER[p])
              )}
            >
              <span className={cn('h-1.5 w-1.5 rounded-full', PLATFORM_COLORS[p])} />
              {p.charAt(0).toUpperCase() + p.slice(1)}
            </button>
          )
        })}
      </div>

      {/* ── Week view ── */}
      {view === 'week' && (
        <div className="rounded-xl border bg-card p-4">
          <WeekCalendar posts={filteredPosts} />
        </div>
      )}

      {/* ── Month grid ── */}
      {view === 'month' && (
        <div className="rounded-xl border bg-card overflow-hidden">
          {/* Day-of-week headers */}
          <div className="grid grid-cols-7 border-b bg-muted/40">
            {WEEK_DAYS.map(d => (
              <div key={d} className="py-2.5 text-center text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                {d}
              </div>
            ))}
          </div>

          {/* Day cells */}
          <div className="grid grid-cols-7">
            {Array.from({ length: startPad }).map((_, i) => (
              <div
                key={`pad-${i}`}
                className={cn('min-h-[130px] border-b bg-muted/5', i < startPad - 1 && 'border-r')}
              />
            ))}

            {days.map((day, i) => {
              const dayPosts = postsForDay(day)
              const isSelected = selectedDay ? isSameDay(day, selectedDay) : false
              const today = isToday(day)
              const past = isPast(day) && !today
              const col = (startPad + i) % 7
              const isLastCol = col === 6

              return (
                <div
                  key={day.toISOString()}
                  onDragOver={onDragOver}
                  onDrop={e => onDrop(e, day)}
                  onClick={() => setSelectedDay(isSelected ? null : day)}
                  className={cn(
                    'min-h-[130px] border-b p-2 cursor-pointer transition-colors group relative',
                    !isLastCol && 'border-r',
                    past && !isSelected && 'bg-muted/5',
                    isSelected
                      ? 'bg-primary/5 ring-1 ring-inset ring-primary/40'
                      : 'hover:bg-muted/20',
                  )}
                >
                  {/* Day number */}
                  <div className="flex items-start justify-between mb-1.5">
                    <span className={cn(
                      'flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold',
                      today
                        ? 'bg-primary text-primary-foreground'
                        : past
                          ? 'text-muted-foreground/50'
                          : 'text-foreground'
                    )}>
                      {format(day, 'd')}
                    </span>
                    {dayPosts.length > 0 && (
                      <span className={cn(
                        'flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[9px] font-bold',
                        isSelected
                          ? 'bg-primary/20 text-primary'
                          : 'bg-muted text-muted-foreground'
                      )}>
                        {dayPosts.length}
                      </span>
                    )}
                  </div>

                  {/* Post chips */}
                  <div className="space-y-0.5">
                    {dayPosts.slice(0, 3).map(post => {
                      const platforms = getPlatforms(post)
                      const firstPlatform = platforms[0]
                      const time = post.scheduled_at ? formatTime(post.scheduled_at) : ''
                      return (
                        <div
                          key={post.id}
                          draggable
                          onDragStart={e => { e.stopPropagation(); onDragStart(e, post.id) }}
                          onClick={e => e.stopPropagation()}
                          className={cn(
                            'flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium cursor-grab active:cursor-grabbing',
                            'truncate select-none transition-opacity',
                            STATUS_CHIP[post.status] ?? STATUS_CHIP.draft,
                          )}
                          title={`${post.content_text ?? ''} · ${time}`}
                        >
                          {firstPlatform && (
                            <span className={cn('w-1.5 h-1.5 rounded-full flex-shrink-0', PLATFORM_COLORS[firstPlatform] ?? 'bg-gray-400')} />
                          )}
                          <span className="truncate flex-1">{post.content_text?.slice(0, 22) ?? '—'}</span>
                          {time && <span className="flex-shrink-0 opacity-60">{time}</span>}
                        </div>
                      )
                    })}
                    {dayPosts.length > 3 && (
                      <div className="text-[10px] text-muted-foreground pl-1.5 font-medium">
                        +{dayPosts.length - 3} more
                      </div>
                    )}
                  </div>

                  {/* Quick-add on hover */}
                  <Link
                    href={`/content/new?date=${format(day, 'yyyy-MM-dd')}`}
                    onClick={e => e.stopPropagation()}
                    className="absolute bottom-1.5 right-1.5 opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground hover:scale-110 transition-transform shadow-sm">
                      <Plus className="h-3 w-3" />
                    </span>
                  </Link>
                </div>
              )
            })}

            {/* Trailing cells */}
            {(() => {
              const total = startPad + days.length
              const remainder = total % 7
              const trailing = remainder === 0 ? 0 : 7 - remainder
              return Array.from({ length: trailing }).map((_, i) => (
                <div key={`trail-${i}`} className={cn('min-h-[130px] border-b bg-muted/5', i < trailing - 1 && 'border-r')} />
              ))
            })()}
          </div>
        </div>
      )}

      {/* ── Day detail panel ── */}
      {selectedDay && (
        <div className="rounded-xl border bg-card overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b bg-muted/30">
            <div>
              <h2 className="font-semibold text-sm">{format(selectedDay, 'EEEE, MMMM d, yyyy')}</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                {selectedDayPosts.length === 0
                  ? 'Nothing scheduled'
                  : `${selectedDayPosts.length} post${selectedDayPosts.length !== 1 ? 's' : ''}`}
              </p>
            </div>
            <Button size="sm" asChild>
              <Link href={`/content/new?date=${format(selectedDay, 'yyyy-MM-dd')}`}>
                <Plus className="mr-1.5 h-3.5 w-3.5" />Schedule post
              </Link>
            </Button>
          </div>

          {selectedDayPosts.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">
              <CalendarDays className="h-8 w-8 mx-auto mb-2 opacity-30" />
              Nothing scheduled for this day.
            </div>
          ) : (
            <div className="divide-y">
              {selectedDayPosts
                .slice()
                .sort((a, b) => (a.scheduled_at ?? '').localeCompare(b.scheduled_at ?? ''))
                .map(post => {
                  const platforms = getPlatforms(post)
                  const time = post.scheduled_at ? formatTime(post.scheduled_at) : ''
                  const StatusIcon = STATUS_ICON[post.status] ?? Clock
                  return (
                    <Link
                      key={post.id}
                      href={`/content/${post.id}/edit`}
                      className="flex items-start gap-3 px-4 py-3 hover:bg-muted/30 transition-colors group"
                    >
                      {/* Time column */}
                      <div className="w-12 flex-shrink-0 text-right">
                        <span className="text-xs font-mono text-muted-foreground">{time}</span>
                      </div>

                      {/* Status dot */}
                      <div className="mt-1 flex-shrink-0">
                        <span className={cn('block h-2.5 w-2.5 rounded-full', STATUS_DOT[post.status] ?? 'bg-gray-400')} />
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-foreground line-clamp-2">
                          {post.content_text ?? 'No content'}
                        </p>
                        <div className="flex flex-wrap items-center gap-2 mt-1.5">
                          {/* Status badge */}
                          <span className={cn(
                            'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold',
                            STATUS_CHIP[post.status]
                          )}>
                            <StatusIcon className="h-2.5 w-2.5" />
                            {post.status}
                          </span>
                          {/* Platform badges */}
                          {platforms.map(p => (
                            <span
                              key={p}
                              className={cn(
                                'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold text-white',
                                PLATFORM_COLORS[p] ?? 'bg-gray-400'
                              )}
                            >
                              {PLATFORM_LABEL[p] ?? p}
                            </span>
                          ))}
                        </div>
                      </div>

                      <ChevronRight className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 mt-1" />
                    </Link>
                  )
                })}
            </div>
          )}
        </div>
      )}

      {/* ── Legend ── */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground px-1">
        <span className="flex items-center gap-1.5 font-medium text-foreground">Status:</span>
        {(['scheduled', 'published', 'failed', 'draft'] as PostStatus[]).map(s => (
          <span key={s} className="flex items-center gap-1.5">
            <span className={cn('h-2 w-2 rounded-full', STATUS_DOT[s])} />{s}
          </span>
        ))}
        <span className="ml-auto">Drag a chip to reschedule</span>
      </div>
    </div>
  )
}
