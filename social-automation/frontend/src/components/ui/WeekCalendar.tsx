'use client'

import { useState } from 'react'
import { format, addDays, startOfDay, isSameDay, isToday } from 'date-fns'
import { cn, formatTime, isOnAthensCalendarDay } from '@/lib/utils'
import { ChevronRight, Plus } from 'lucide-react'
import Link from 'next/link'
import type { Post, PostTarget, SocialAccount } from '@/types'

const PLATFORM_COLORS: Record<string, string> = {
  linkedin:  'bg-blue-600',
  twitter:   'bg-sky-500',
  instagram: 'bg-pink-500',
  facebook:  'bg-blue-700',
  threads:   'bg-gray-700',
}

function getPlatforms(post: Post): string[] {
  const via_targets = post.targets
    ?.map((t: PostTarget) => (t.social_account as SocialAccount | undefined)?.platform)
    .filter(Boolean) as string[] | undefined
  return via_targets?.length ? Array.from(new Set(via_targets)) : []
}

interface WeekCalendarProps {
  posts: Post[]
}

export function WeekCalendar({ posts }: WeekCalendarProps) {
  const today = startOfDay(new Date())
  const days = Array.from({ length: 7 }, (_, i) => addDays(today, i))

  const [selectedDay, setSelectedDay] = useState<Date | null>(null)

  const postsForDay = (day: Date) =>
    posts.filter((p) => p.scheduled_at && isOnAthensCalendarDay(p.scheduled_at, day))

  const selectedPosts = selectedDay ? postsForDay(selectedDay) : []

  return (
    <div className="space-y-3">
      {/* 7-day strip */}
      <div className="grid grid-cols-7 gap-1">
        {days.map((day) => {
          const dayPosts = postsForDay(day)
          const platforms = Array.from(new Set(dayPosts.flatMap(getPlatforms)))
          const isSelected = selectedDay ? isSameDay(day, selectedDay) : false
          const today_ = isToday(day)

          return (
            <button
              key={day.toISOString()}
              onClick={() => setSelectedDay(isSelected ? null : day)}
              className={cn(
                'flex flex-col items-center gap-1.5 rounded-xl p-2 text-center transition-colors',
                isSelected
                  ? 'bg-primary text-primary-foreground'
                  : today_
                    ? 'bg-primary/10 text-primary'
                    : 'hover:bg-accent text-muted-foreground hover:text-foreground'
              )}
            >
              <span className="text-[10px] font-medium uppercase tracking-wide">
                {format(day, 'EEE')}
              </span>
              <span className={cn(
                'flex h-7 w-7 items-center justify-center rounded-full text-sm font-semibold',
                today_ && !isSelected && 'bg-primary text-primary-foreground',
              )}>
                {format(day, 'd')}
              </span>
              {/* Platform dots */}
              <div className="flex flex-wrap justify-center gap-0.5 min-h-[10px]">
                {platforms.slice(0, 4).map((p) => (
                  <span
                    key={p}
                    className={cn('h-1.5 w-1.5 rounded-full', PLATFORM_COLORS[p] ?? 'bg-gray-400')}
                    title={p}
                  />
                ))}
                {platforms.length > 4 && (
                  <span className="text-[8px] leading-none">+{platforms.length - 4}</span>
                )}
              </div>
              {dayPosts.length > 0 && (
                <span className={cn(
                  'text-[9px] font-medium',
                  isSelected ? 'text-primary-foreground/80' : 'text-muted-foreground'
                )}>
                  {dayPosts.length} post{dayPosts.length !== 1 ? 's' : ''}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Day detail panel */}
      {selectedDay && (
        <div className="rounded-xl border bg-muted/30 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              {format(selectedDay, 'EEEE, MMMM d')}
            </span>
            <Link
              href="/content/new"
              className="flex items-center gap-1 text-xs text-primary hover:underline"
            >
              <Plus className="h-3 w-3" />
              Add post
            </Link>
          </div>

          {selectedPosts.length === 0 ? (
            <p className="text-xs text-muted-foreground py-2">
              Nothing scheduled — <Link href="/content/new" className="text-primary hover:underline">create a post</Link>
            </p>
          ) : (
            <div className="space-y-1.5">
              {selectedPosts.map((post) => {
                const platforms = getPlatforms(post)
                return (
                  <Link
                    key={post.id}
                    href={`/content/${post.id}/edit`}
                    className="flex items-start gap-2.5 rounded-lg border bg-background px-3 py-2 text-xs hover:bg-accent transition-colors group"
                  >
                    {/* Platform dots column */}
                    <div className="flex flex-col gap-0.5 pt-0.5">
                      {platforms.slice(0, 3).map((p) => (
                        <span key={p} className={cn('h-2 w-2 rounded-full', PLATFORM_COLORS[p] ?? 'bg-gray-400')} title={p} />
                      ))}
                    </div>
                    {/* Content preview */}
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-foreground truncate">
                        {post.content_text?.slice(0, 60) ?? 'No content'}
                        {(post.content_text?.length ?? 0) > 60 ? '…' : ''}
                      </p>
                      <p className="text-muted-foreground mt-0.5">
                        {post.scheduled_at
                          ? formatTime(post.scheduled_at)
                          : '—'}
                        {platforms.length > 0 && ` · ${platforms.join(', ')}`}
                      </p>
                    </div>
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 mt-0.5" />
                  </Link>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
