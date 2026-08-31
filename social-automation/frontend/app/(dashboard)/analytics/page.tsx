'use client'

import { useState, useMemo } from 'react'
import {
  TrendingUp, TrendingDown, Download, BarChart3, Plus, ArrowLeftRight,
  Users, Heart, UserCheck, Send, RefreshCw, FileText,
} from 'lucide-react'
import toast from 'react-hot-toast'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { Skeleton } from '@/components/ui/Skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { EmptyState } from '@/components/ui/EmptyState'
import { useOverviewMetrics, usePlatformMetrics, useTopPosts, useEngagementTrends } from '@/hooks/useQueries'
import type { PlatformMetrics, TopPost } from '@/types'
import { formatRelativeTime, cn } from '@/lib/utils'
import { analyticsApi } from '@/services/api'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, AreaChart, Area, Cell, Line,
} from 'recharts'
import { format } from 'date-fns'

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']

const PLATFORM_COLOR: Record<string, string> = {
  linkedin: '#0077b5',
  twitter: '#1da1f2',
  instagram: '#e1306c',
  facebook: '#1877f2',
  threads: '#000',
  tiktok: '#ff0050',
}

function sanitizePostContent(text: string | null | undefined): string {
  if (!text) return 'Untitled'
  // Strip LinkedIn URN IDs like urn:li:ugcPost:123 or urn:li:share:456
  if (/^urn:li:/i.test(text.trim())) return 'LinkedIn post'
  return text
}

function engagementRateColor(rate: number | null | undefined): string {
  if (rate == null) return 'text-muted-foreground'
  if (rate >= 0.03) return 'text-green-600 dark:text-green-400'
  if (rate >= 0.01) return 'text-amber-600 dark:text-amber-400'
  return 'text-red-600 dark:text-red-400'
}

function RankBadge({ n }: { n: number }) {
  const medals = ['🥇', '🥈', '🥉']
  if (n < 3) return <span className="text-xl w-8 text-center">{medals[n]}</span>
  return <span className="text-base font-bold text-muted-foreground/40 w-8 text-center">#{n + 1}</span>
}

export default function AnalyticsPage() {
  const [days, setDays] = useState(30)
  const [platformFilter, setPlatformFilter] = useState('')
  const [compareMode, setCompareMode] = useState(false)
  const [syncing, setSyncing] = useState(false)

  const { data: overview, isLoading: overviewLoading } = useOverviewMetrics(days)
  const { data: platformData, isLoading: platformLoading } = usePlatformMetrics(days)
  const { data: topPosts, isLoading: postsLoading } = useTopPosts(10, platformFilter || undefined, days)
  const { data: rawTrend } = useEngagementTrends(
    compareMode ? days * 2 : days,
    platformFilter || undefined
  )

  const { currentTrend, deltaEngagement } = useMemo(() => {
    const trend = (rawTrend || []) as Array<{ date: string; value: number; likes?: number; comments?: number; shares?: number; clicks?: number }>
    if (!compareMode || trend.length < 2) {
      return { currentTrend: trend, previousTrend: [], currentSum: 0, previousSum: 0, deltaEngagement: 0 }
    }
    const half = Math.ceil(trend.length / 2)
    const prev = trend.slice(0, half)
    const curr = trend.slice(half)
    const sumArr = (a: typeof trend) => a.reduce((acc, d) => acc + (d.value ?? 0), 0)
    const cs = sumArr(curr)
    const ps = sumArr(prev)
    const delta = ps === 0 ? 0 : ((cs - ps) / ps) * 100
    const merged = curr.map((d, i) => ({ ...d, prev: prev[i]?.value ?? 0 }))
    return { currentTrend: merged, previousTrend: prev, currentSum: cs, previousSum: ps, deltaEngagement: delta }
  }, [rawTrend, compareMode])

  const engagementTrend = compareMode
    ? currentTrend
    : ((rawTrend || []) as Array<{ date: string; value: number; likes?: number; comments?: number; shares?: number }>)

  const hasTrendData = engagementTrend.some((d) => (d.value ?? 0) > 0)

  const avgEngagement = useMemo(() => {
    const published = overview?.published_posts ?? 0
    const total = overview?.total_engagement ?? 0
    if (published === 0) return 0
    return Math.round(total / published)
  }, [overview])

  if (overviewLoading) {
    return (
      <div className="space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <Skeleton className="h-8 w-32 mb-2" />
            <Skeleton className="h-4 w-48" />
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: '1rem' }}>
          {[1, 2, 3, 4].map((i) => (
            <Card key={i}>
              <CardContent className="p-6">
                <Skeleton className="h-4 w-24 mb-2" />
                <Skeleton className="h-8 w-16" />
              </CardContent>
            </Card>
          ))}
        </div>
        <Card>
          <CardContent className="p-6 h-72">
            <Skeleton className="h-full w-full" />
          </CardContent>
        </Card>
      </div>
    )
  }

  const platformMetrics = (platformData?.map((p: PlatformMetrics, i: number) => ({
    ...p,
    color: PLATFORM_COLOR[p.platform] ?? COLORS[i % COLORS.length],
  })) || []) as (PlatformMetrics & { color: string })[]

  const metrics = [
    {
      name: 'Total Engagement',
      value: (overview?.total_engagement ?? 0).toLocaleString(),
      change: compareMode ? deltaEngagement : null,
      icon: Heart,
      color: 'text-green-500',
      bg: 'bg-green-500/10',
    },
    {
      name: 'Posts Published',
      value: (overview?.published_posts ?? 0).toLocaleString(),
      change: null as number | null,
      icon: Send,
      color: 'text-blue-500',
      bg: 'bg-blue-500/10',
    },
    {
      name: 'Avg Eng / Post',
      value: avgEngagement.toLocaleString(),
      change: null as number | null,
      icon: TrendingUp,
      color: 'text-purple-500',
      bg: 'bg-purple-500/10',
    },
    {
      name: 'Connected Accounts',
      value: (overview?.connected_accounts ?? 0).toLocaleString(),
      change: null as number | null,
      icon: Users,
      color: 'text-orange-500',
      bg: 'bg-orange-500/10',
    },
    {
      name: 'Total Followers',
      value: (overview?.total_followers ?? 0).toLocaleString(),
      change: null as number | null,
      icon: UserCheck,
      color: 'text-pink-500',
      bg: 'bg-pink-500/10',
    },
  ]

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Analytics</h1>
          <p className="text-muted-foreground mt-1">Track your social media performance</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={days.toString()} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="Time range" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">Last 7 days</SelectItem>
              <SelectItem value="30">Last 30 days</SelectItem>
              <SelectItem value="90">Last 90 days</SelectItem>
              <SelectItem value="365">Last year</SelectItem>
            </SelectContent>
          </Select>
          <Select value={platformFilter} onValueChange={setPlatformFilter}>
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="All Platforms" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">All Platforms</SelectItem>
              <SelectItem value="linkedin">LinkedIn</SelectItem>
              <SelectItem value="twitter">Twitter/X</SelectItem>
              <SelectItem value="instagram">Instagram</SelectItem>
              <SelectItem value="facebook">Facebook</SelectItem>
              <SelectItem value="threads">Threads</SelectItem>
              <SelectItem value="tiktok">TikTok</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant={compareMode ? 'default' : 'outline'}
            size="sm"
            onClick={() => setCompareMode(v => !v)}
            className="gap-1.5"
          >
            <ArrowLeftRight className="h-4 w-4" />
            {compareMode ? 'Comparing' : 'Compare'}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={syncing}
            onClick={async () => {
              setSyncing(true)
              try {
                const res = await analyticsApi.syncFromPlatforms({ days, async_mode: true })
                const data = res.data as { status?: string; task_id?: string }
                toast.success(
                  data?.status === 'queued'
                    ? 'Sync queued — metrics will update shortly'
                    : 'Sync started'
                )
              } catch {
                toast.error('Failed to start sync')
              } finally {
                setSyncing(false)
              }
            }}
          >
            <RefreshCw className={cn('mr-1.5 h-4 w-4', syncing && 'animate-spin')} />
            Sync
          </Button>
        </div>
      </div>

      {/* KPI Cards — 5 metrics in a responsive grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: '1rem' }}>
        {metrics.map((metric) => (
          <Card key={metric.name}>
            <CardContent className="p-5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-xs font-medium text-muted-foreground truncate">{metric.name}</p>
                  <p className="text-2xl font-bold mt-1 tabular-nums">{metric.value}</p>
                </div>
                <div className={cn('p-2 rounded-full shrink-0', metric.bg)}>
                  <metric.icon className={cn('h-4 w-4', metric.color)} />
                </div>
              </div>
              {metric.change !== null && (
                <div className="mt-3 flex items-center gap-1 text-xs">
                  {metric.change >= 0 ? (
                    <>
                      <TrendingUp className="h-3 w-3 text-green-500" />
                      <span className="text-green-500">+{metric.change.toFixed(1)}%</span>
                      <span className="text-muted-foreground">vs prev {days}d</span>
                    </>
                  ) : (
                    <>
                      <TrendingDown className="h-3 w-3 text-red-500" />
                      <span className="text-red-500">{metric.change.toFixed(1)}%</span>
                      <span className="text-muted-foreground">vs prev {days}d</span>
                    </>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Charts — 2-col on wide screens, 1-col on narrow */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '1.5rem' }}>
        {/* Engagement Over Time */}
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between">
              <div>
                <CardTitle>Engagement Over Time</CardTitle>
                <CardDescription>
                  Daily total engagements
                  {compareMode && ` — current vs prev ${days}d`}
                </CardDescription>
              </div>
              {compareMode && (
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block h-0.5 w-5 bg-blue-500 rounded" />Current
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block h-0.5 w-5 border-t-2 border-dashed border-blue-300" />Previous
                  </span>
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {!hasTrendData ? (
              <div style={{ height: '18rem', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', gap: '0.5rem' }}>
                <BarChart3 className="h-10 w-10 text-muted-foreground/30" />
                <p className="text-sm font-medium text-muted-foreground">No engagement data yet</p>
                <p className="text-xs text-muted-foreground/70">Publish posts and sync from LinkedIn to see data here.</p>
              </div>
            ) : (
              <div style={{ height: '18rem' }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={engagementTrend}>
                    <defs>
                      <linearGradient id="engGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.25} />
                        <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                    <XAxis
                      dataKey="date"
                      tickFormatter={(v) => { try { return format(new Date(v as string), 'MMM d') } catch { return v as string } }}
                      className="text-xs"
                    />
                    <YAxis className="text-xs" allowDecimals={false} />
                    <Tooltip
                      contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px', fontSize: 12 }}
                      formatter={(value: number, name: string) => [
                        value.toLocaleString(),
                        name === 'prev' ? 'Prev period' : 'Total',
                      ]}
                      labelFormatter={(label: string) => { try { return format(new Date(label), 'MMM d, yyyy') } catch { return label } }}
                    />
                    <Area type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={2} fillOpacity={1} fill="url(#engGrad)" name="Total" />
                    {compareMode && (
                      <Line type="monotone" dataKey="prev" stroke="#93c5fd" strokeWidth={1.5} strokeDasharray="4 3" dot={false} name="prev" />
                    )}
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Platform Performance */}
        <Card>
          <CardHeader>
            <CardTitle>Platform Performance</CardTitle>
            <CardDescription>Total engagement per platform</CardDescription>
          </CardHeader>
          <CardContent>
            {platformMetrics.length === 0 ? (
              <div style={{ height: '18rem', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', gap: '0.5rem' }}>
                <BarChart3 className="h-10 w-10 text-muted-foreground/30" />
                <p className="text-sm font-medium text-muted-foreground">No platform data</p>
                <p className="text-xs text-muted-foreground/70">Connect social accounts to see platform metrics.</p>
              </div>
            ) : (
              <div style={{ height: '18rem' }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={platformMetrics} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                    <XAxis type="number" className="text-xs" allowDecimals={false} />
                    <YAxis dataKey="platform" type="category" width={80} className="text-xs capitalize" />
                    <Tooltip
                      contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px', fontSize: 12 }}
                      formatter={(value: number) => [value.toLocaleString(), 'Engagements']}
                    />
                    <Bar dataKey="total_engagement" radius={[0, 4, 4, 0]}>
                      {platformMetrics.map((p, i) => (
                        <Cell key={i} fill={p.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Impressions by Platform — full width */}
        <Card style={{ gridColumn: '1 / -1' }}>
          <CardHeader>
            <CardTitle>Impressions by Platform</CardTitle>
            <CardDescription>Total impressions for the selected period</CardDescription>
          </CardHeader>
          <CardContent>
            {platformMetrics.length === 0 || platformMetrics.every(p => !p.total_impressions) ? (
              <div style={{ height: '18rem', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', gap: '0.5rem' }}>
                <BarChart3 className="h-10 w-10 text-muted-foreground/30" />
                <p className="text-sm font-medium text-muted-foreground">No impressions data</p>
                <p className="text-xs text-muted-foreground/70">Impression data arrives after publishing.</p>
              </div>
            ) : (
              <div style={{ height: '18rem' }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={platformMetrics}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                    <XAxis dataKey="platform" className="text-xs capitalize" />
                    <YAxis className="text-xs" allowDecimals={false} />
                    <Tooltip
                      contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px', fontSize: 12 }}
                      formatter={(value: number) => [value.toLocaleString(), 'Impressions']}
                    />
                    <Bar dataKey="total_impressions" radius={[4, 4, 0, 0]}>
                      {platformMetrics.map((p, i) => (
                        <Cell key={i} fill={p.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Platform Breakdown Table — full width */}
        <Card style={{ gridColumn: '1 / -1' }}>
          <CardHeader>
            <CardTitle>Platform Breakdown</CardTitle>
            <CardDescription>Detailed metrics per platform</CardDescription>
          </CardHeader>
          <CardContent>
            {platformLoading ? (
              <div className="space-y-3">
                {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
              </div>
            ) : platformMetrics.length === 0 ? (
              <p className="text-sm text-muted-foreground py-8 text-center">No platform data for this period.</p>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Platform</TableHead>
                      <TableHead className="text-right">Impressions</TableHead>
                      <TableHead className="text-right">Engagement</TableHead>
                      <TableHead className="text-right">Eng. Rate</TableHead>
                      <TableHead className="text-right">Published</TableHead>
                      <TableHead className="text-right">Total Posts</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {platformMetrics.map((p) => (
                      <TableRow key={p.platform}>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <div
                              className="w-7 h-7 rounded-full flex items-center justify-center text-white text-xs font-bold shrink-0"
                              style={{ backgroundColor: p.color }}
                            >
                              {p.platform[0].toUpperCase()}
                            </div>
                            <span className="font-medium capitalize">{p.platform}</span>
                          </div>
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">{(p.total_impressions ?? 0).toLocaleString()}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{(p.total_engagement ?? 0).toLocaleString()}</TableCell>
                        <TableCell className={cn('text-right font-mono text-sm font-medium', engagementRateColor(p.engagement_rate))}>
                          {p.engagement_rate != null ? `${(p.engagement_rate * 100).toFixed(1)}%` : '—'}
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">{p.published_count ?? 0}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{p.posts_count ?? 0}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Top Posts */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between">
          <div>
            <CardTitle>Top Performing Posts</CardTitle>
            <CardDescription>Best content by total engagement</CardDescription>
          </div>
          {platformFilter && (
            <Badge variant="secondary" className="capitalize">{platformFilter} only</Badge>
          )}
        </CardHeader>
        <CardContent>
          {postsLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => <Skeleton key={i} className="h-14 w-full" />)}
            </div>
          ) : !topPosts?.length ? (
            <EmptyState
              icon={BarChart3}
              title="No data for this period"
              description="Publish posts to start seeing engagement metrics. Data appears within 24 hours of publishing."
              primaryAction={{ label: 'Create a post', href: '/content/new', icon: Plus }}
              className="py-10"
            />
          ) : (
            <div className="space-y-1">
              {topPosts.map((post: TopPost, index: number) => {
                const content = sanitizePostContent(post.content_text)
                const platformColor = PLATFORM_COLOR[post.platform] ?? '#6366f1'
                return (
                  <Link
                    key={`${post.post_id}-${index}`}
                    href={`/content/${post.post_id}/edit`}
                    className="flex items-center justify-between p-3 rounded-lg hover:bg-accent transition-colors group"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <RankBadge n={index} />
                      <div className="h-9 w-9 rounded-lg flex items-center justify-center shrink-0 text-white text-xs font-bold" style={{ backgroundColor: platformColor }}>
                        {post.platform[0].toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <p className="font-medium truncate text-sm group-hover:text-primary transition-colors">
                          {content.length > 90 ? content.slice(0, 90) + '…' : content}
                        </p>
                        <p className="text-xs text-muted-foreground capitalize">
                          {post.platform}
                          {post.published_at ? ` · ${formatRelativeTime(post.published_at)}` : ''}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3 ml-4 shrink-0">
                      <div className="text-right hidden sm:block">
                        <p className="text-sm font-bold tabular-nums">{(post.engagement ?? 0).toLocaleString()}</p>
                        <p className="text-xs text-muted-foreground">engagements</p>
                      </div>
                      <Badge variant="outline" className="hidden sm:inline-flex">
                        <Heart className="h-3 w-3 mr-1" />
                        {(post.engagement ?? 0).toLocaleString()}
                      </Badge>
                    </div>
                  </Link>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
