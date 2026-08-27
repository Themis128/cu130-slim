'use client'

import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import type { TopPost } from '@/types'

// Industry benchmark scores [dayIndex 0=Mon…6=Sun][slotIndex 0=Morning…3=Evening]
// Source: Sprout Social / Later optimal send-time research 2024
const BENCHMARKS: Record<string, number[][]> = {
  linkedin:  [[70,80,60,30],[75,95,70,35],[72,90,65,30],[70,85,65,30],[65,70,55,25],[20,20,15,10],[15,15,10,5]],
  twitter:   [[75,85,80,70],[70,90,85,70],[70,90,80,65],[70,90,85,70],[65,80,70,65],[40,50,55,60],[35,45,50,55]],
  instagram: [[75,90,70,65],[70,80,65,60],[80,85,75,85],[75,85,70,70],[70,75,80,65],[60,65,60,55],[50,55,55,50]],
  facebook:  [[65,70,75,55],[70,85,90,60],[65,80,85,55],[70,85,88,60],[65,75,70,55],[45,50,50,45],[40,45,45,40]],
  threads:   [[70,85,70,75],[65,80,65,70],[75,85,75,80],[70,82,70,72],[65,75,70,68],[50,55,55,55],[45,50,50,50]],
}

// Blend platforms for "all"
const ALL_BENCHMARK: number[][] = Array.from({ length: 7 }, (_, d) =>
  Array.from({ length: 4 }, (_, s) => {
    const vals = Object.values(BENCHMARKS).map((b) => b[d][s])
    return Math.round(vals.reduce((a, b) => a + b, 0) / vals.length)
  })
)

const DAYS  = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
const SLOTS = [
  { label: 'Morning',   range: '6–10 am'  },
  { label: 'Midday',    range: '10 am–2 pm' },
  { label: 'Afternoon', range: '2–6 pm'   },
  { label: 'Evening',   range: '6–10 pm'  },
]

const PLATFORMS = [
  { id: 'all',       name: 'All Platforms' },
  { id: 'linkedin',  name: 'LinkedIn'   },
  { id: 'twitter',   name: 'Twitter / X' },
  { id: 'instagram', name: 'Instagram'  },
  { id: 'facebook',  name: 'Facebook'   },
  { id: 'threads',   name: 'Threads'    },
]

function scoreToCell(score: number) {
  if (score >= 85) return 'bg-primary text-primary-foreground'
  if (score >= 70) return 'bg-primary/60 text-primary-foreground'
  if (score >= 55) return 'bg-primary/35 text-foreground'
  if (score >= 40) return 'bg-primary/15 text-muted-foreground'
  return 'bg-muted/40 text-muted-foreground/60'
}

interface PostingHeatmapProps {
  topPosts?: TopPost[]
}

export function PostingHeatmap({ topPosts = [] }: PostingHeatmapProps) {
  const [platform, setPlatform] = useState('all')

  // Build score matrix from real data when available, else fall back to benchmarks
  const matrix: number[][] = useMemo(() => {
    const relevant = platform === 'all'
      ? topPosts
      : topPosts.filter((p) => p.platform === platform)

    if (relevant.length < 3) {
      // Not enough real data — use benchmarks
      return platform === 'all' ? ALL_BENCHMARK : (BENCHMARKS[platform] ?? ALL_BENCHMARK)
    }

    // Accumulate real engagement by day × slot
    const raw: number[][] = Array.from({ length: 7 }, () => Array(4).fill(0))
    const counts: number[][] = Array.from({ length: 7 }, () => Array(4).fill(0))

    for (const post of relevant) {
      if (!post.published_at) continue
      const d = new Date(post.published_at)
      const dayIdx = (d.getDay() + 6) % 7  // 0=Mon
      const hour   = d.getHours()
      const slotIdx = hour < 10 ? 0 : hour < 14 ? 1 : hour < 18 ? 2 : 3
      const eng = post.engagement ?? 0
      raw[dayIdx][slotIdx]    += eng
      counts[dayIdx][slotIdx] += 1
    }

    // Normalise to 0-100
    const avgs = raw.map((row, d) => row.map((v, s) => counts[d][s] > 0 ? v / counts[d][s] : 0))
    const maxVal = Math.max(...avgs.flat(), 1)
    return avgs.map((row) => row.map((v) => Math.round((v / maxVal) * 100)))
  }, [topPosts, platform])

  // Find top-3 cells
  const allCells = matrix.flatMap((row, d) => row.map((score, s) => ({ d, s, score })))
  const top3 = [...allCells].sort((a, b) => b.score - a.score).slice(0, 3)
  const isTop = (d: number, s: number) => top3.some((c) => c.d === d && c.s === s)

  const usingBenchmarks = topPosts.filter(p => platform === 'all' || p.platform === platform).length < 3

  return (
    <div className="space-y-3">
      {/* Platform filter */}
      <div className="flex flex-wrap gap-1.5">
        {PLATFORMS.map((p) => (
          <button
            key={p.id}
            onClick={() => setPlatform(p.id)}
            className={cn(
              'rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors',
              platform === p.id
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:bg-accent hover:text-foreground'
            )}
          >
            {p.name}
          </button>
        ))}
      </div>

      {/* Grid: rows = time slots, cols = days */}
      <div className="overflow-x-auto">
        <div className="min-w-[340px]">
          {/* Day headers */}
          <div className="grid grid-cols-[72px_repeat(7,1fr)] gap-1 mb-1">
            <div />
            {DAYS.map((d) => (
              <div key={d} className="text-center text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">
                {d}
              </div>
            ))}
          </div>

          {/* Slot rows */}
          {SLOTS.map((slot, s) => (
            <div key={slot.label} className="grid grid-cols-[72px_repeat(7,1fr)] gap-1 mb-1">
              <div className="flex flex-col justify-center pr-2">
                <span className="text-[11px] font-medium text-muted-foreground leading-tight">{slot.label}</span>
                <span className="text-[9px] text-muted-foreground/60 leading-tight">{slot.range}</span>
              </div>
              {DAYS.map((_, d) => {
                const score = matrix[d]?.[s] ?? 0
                const top = isTop(d, s)
                return (
                  <div
                    key={d}
                    title={`${DAYS[d]} ${slot.label}: score ${score}`}
                    className={cn(
                      'relative h-8 rounded-md flex items-center justify-center text-[10px] font-medium transition-all',
                      scoreToCell(score),
                      top && 'ring-2 ring-primary ring-offset-1 ring-offset-background'
                    )}
                  >
                    {top && (
                      <span className="absolute -top-1 -right-1 h-2.5 w-2.5 rounded-full bg-amber-400 border border-background" />
                    )}
                    {score >= 55 && <span>{score}</span>}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Legend + source note */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground">Low</span>
          {[15, 40, 60, 75, 92].map((s) => (
            <div key={s} className={cn('h-3 w-5 rounded-sm', scoreToCell(s))} />
          ))}
          <span className="text-[10px] text-muted-foreground">High</span>
        </div>
        <span className="text-[10px] text-muted-foreground italic">
          {usingBenchmarks
            ? '★ industry benchmarks — updates with your data'
            : '★ based on your engagement history'}
        </span>
      </div>
    </div>
  )
}
