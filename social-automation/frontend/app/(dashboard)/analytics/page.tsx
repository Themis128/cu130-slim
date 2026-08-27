'use client'

import { useState, useMemo } from 'react'
import { TrendingUp, TrendingDown, Download, BarChart3, Plus, ArrowLeftRight } from 'lucide-react'
import toast from 'react-hot-toast'
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
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  AreaChart,
  Area,
  Cell,
  Legend,
} from 'recharts'
import { format } from 'date-fns'

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']

export default function AnalyticsPage() {
  const [days, setDays] = useState(30)
  const [platformFilter, setPlatformFilter] = useState('')
  const [compareMode, setCompareMode] = useState(false)

  const { data: overview, isLoading: overviewLoading } = useOverviewMetrics(days)
  const { data: platformData, isLoading: platformLoading } = usePlatformMetrics(days)
  const { data: topPosts, isLoading: postsLoading } = useTopPosts(10, platformFilter || undefined, days)
  // When compare mode is on, fetch 2× the period so we can split it
  const { data: rawTrend } = useEngagementTrends(
    compareMode ? days * 2 : days,
    platformFilter || undefined
  )

  // Split trend into current / previous halves for comparison
  const { currentTrend, previousTrend, currentSum, previousSum, deltaEngagement } = useMemo(() => {
    const trend = (rawTrend || []) as Array<{ date: string; value: number }>
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
    // Normalise previous period labels to match current period labels
    const normalised = prev.map((d, i) => ({ date: curr[i]?.date ?? d.date, prev: d.value ?? 0 }))
    const merged = curr.map((d, i) => ({ date: d.date, value: d.value ?? 0, prev: normalised[i]?.prev ?? 0 }))
    return { currentTrend: merged, previousTrend: normalised, currentSum: cs, previousSum: ps, deltaEngagement: delta }
  }, [rawTrend, compareMode])

  const engagementTrend = compareMode ? currentTrend : ((rawTrend || []) as Array<{ date: string; value: number }>)

  if (overviewLoading) {
    return (
      <div className="space-y-6">
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
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
          <CardContent className="p-6 h-96">
            <Skeleton className="h-full w-full" />
          </CardContent>
        </Card>
      </div>
    )
  }

  const metrics = [
    {
      name: 'Connected Accounts',
      value: overview?.connected_accounts?.toLocaleString() || '0',
      change: null as number | null,
      icon: TrendingUp,
      color: 'text-blue-500',
      bg: 'bg-blue-500/10',
    },
    {
      name: 'Total Engagement',
      value: overview?.total_engagement?.toLocaleString() || '0',
      change: compareMode ? deltaEngagement : null,
      icon: TrendingUp,
      color: 'text-green-500',
      bg: 'bg-green-500/10',
    },
    {
      name: 'Total Followers',
      value: overview?.total_followers?.toLocaleString() || '0',
      change: null as number | null,
      icon: TrendingUp,
      color: 'text-purple-500',
      bg: 'bg-purple-500/10',
    },
    {
      name: 'Posts Published',
      value: overview?.published_posts?.toLocaleString() || '0',
      change: null as number | null,
      icon: TrendingUp,
      color: 'text-orange-500',
      bg: 'bg-orange-500/10',
    },
  ]

  const platformMetrics = platformData?.map((p: PlatformMetrics, i: number) => ({
    ...p,
    color: COLORS[i % COLORS.length],
  })) || []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Analytics</h1>
          <p className="text-muted-foreground mt-1">Track your social media performance</p>
        </div>
        <div className="flex items-center gap-4">
          <Select value={days.toString()} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-[160px]">
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
            <SelectTrigger className="w-[160px]">
              <SelectValue placeholder="All Platforms" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">All Platforms</SelectItem>
              <SelectItem value="linkedin">LinkedIn</SelectItem>
              <SelectItem value="twitter">Twitter/X</SelectItem>
              <SelectItem value="instagram">Instagram</SelectItem>
              <SelectItem value="facebook">Facebook</SelectItem>
              <SelectItem value="threads">Threads</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant={compareMode ? 'default' : 'outline'}
            onClick={() => setCompareMode(v => !v)}
            className="gap-1.5"
          >
            <ArrowLeftRight className="h-4 w-4" />
            {compareMode ? 'Comparing periods' : 'Compare periods'}
          </Button>
          <Button
            variant="outline"
            onClick={async () => {
              try {
                const res = await analyticsApi.syncFromPlatforms({ days, async_mode: true })
                const data = res.data as { status?: string; task_id?: string }
                toast.success(
                  data?.status === 'queued'
                    ? 'Analytics sync queued — LinkedIn metrics will land in Postgres shortly'
                    : 'Analytics sync started'
                )
              } catch {
                toast.error('Failed to start analytics sync')
              }
            }}
          >
            <Download className="mr-2 h-4 w-4" />
            Sync from LinkedIn
          </Button>
          <Button variant="outline" onClick={() => toast('Use Sync then Export via API /analytics/reports/export')}>
            <Download className="mr-2 h-4 w-4" />
            Export
          </Button>
        </div>
      </div>

      {/* Key Metrics */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {metrics.map((metric) => (
          <Card key={metric.name}>
            <CardContent className="p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-muted-foreground">{metric.name}</p>
                  <p className="text-3xl font-bold mt-1">{metric.value}</p>
                </div>
                <div className={cn('p-3 rounded-full', metric.bg)}>
                  <metric.icon className={cn('h-6 w-6', metric.color)} />
                </div>
              </div>
              {metric.change !== null && (
                <div className="mt-4 flex items-center gap-1 text-sm">
                  {metric.change >= 0 ? (
                    <>
                      <TrendingUp className="h-3.5 w-3.5 text-green-500" />
                      <span className="text-green-500">+{metric.change.toFixed(1)}%</span>
                      <span className="text-muted-foreground">vs prev {days}d</span>
                    </>
                  ) : (
                    <>
                      <TrendingDown className="h-3.5 w-3.5 text-red-500" />
                      <span className="text-red-500">{metric.change.toFixed(1)}%</span>
                      <span className="text-muted-foreground">vs prev {days}d</span>
                    </>
                  )}
                </div>
              )}
              {metric.change === null && compareMode && (
                <div className="mt-4 text-xs text-muted-foreground">—</div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Charts */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Engagement Over Time */}
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between">
              <div>
                <CardTitle>Engagement Over Time</CardTitle>
                <CardDescription>
                  Daily engagement across all platforms
                  {compareMode && ` — current vs prev ${days}d`}
                </CardDescription>
              </div>
              {compareMode && (
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1.5"><span className="inline-block h-0.5 w-5 bg-blue-500 rounded" />Current</span>
                  <span className="flex items-center gap-1.5"><span className="inline-block h-0.5 w-5 border-t-2 border-dashed border-blue-300 rounded" />Previous</span>
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={engagementTrend}>
                  <defs>
                    <linearGradient id="colorEngagement" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                  <XAxis dataKey="date" tickFormatter={(v) => { try { return format(new Date(v as string), 'MMM d') } catch { return v as string } }} className="text-xs" />
                  <YAxis className="text-xs" />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px' }}
                    formatter={(value: number, name: string) => [value.toLocaleString(), name === 'prev' ? 'Prev period' : 'Engagements']}
                  />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#colorEngagement)"
                  />
                  {compareMode && (
                    <Line
                      type="monotone"
                      dataKey="prev"
                      stroke="#93c5fd"
                      strokeWidth={1.5}
                      strokeDasharray="4 3"
                      dot={false}
                    />
                  )}
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        {/* Platform Comparison */}
        <Card>
          <CardHeader>
            <CardTitle>Platform Performance</CardTitle>
            <CardDescription>Engagement by platform</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={platformMetrics} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                  <XAxis type="number" className="text-xs" />
                  <YAxis dataKey="platform" type="category" width={80} className="text-xs" />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px' }}
                    formatter={(value: number) => [value.toLocaleString(), 'Engagements']}
                  />
                  <Bar dataKey="total_engagement" radius={[0, 4, 4, 0]}>
                    {platformMetrics.map((_: PlatformMetrics, i: number) => (
                      <Cell key={i} fill={COLORS[i % COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        {/* Impressions by Platform */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Impressions by Platform</CardTitle>
            <CardDescription>Total impressions per platform for the selected period</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={platformMetrics}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                  <XAxis dataKey="platform" className="text-xs" />
                  <YAxis className="text-xs" />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px' }}
                    formatter={(value: number) => [value.toLocaleString(), 'Impressions']}
                  />
                  <Bar dataKey="total_impressions" radius={[4, 4, 0, 0]}>
                    {platformMetrics.map((_: PlatformMetrics, i: number) => (
                      <Cell key={i} fill={COLORS[i % COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        {/* Platform Breakdown */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Platform Breakdown</CardTitle>
            <CardDescription>Detailed metrics per platform</CardDescription>
          </CardHeader>
          <CardContent>
            {platformLoading ? (
              <div className="space-y-4">
                {[1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : (
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
                  {platformMetrics.map((p: PlatformMetrics, i: number) => (
                    <TableRow key={p.platform}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-bold" style={{ backgroundColor: COLORS[i % COLORS.length] }}>
                            {p.platform[0].toUpperCase()}
                          </div>
                          <span className="font-medium capitalize">{p.platform}</span>
                        </div>
                      </TableCell>
                      <TableCell className="text-right font-mono">{p.total_impressions?.toLocaleString() || '0'}</TableCell>
                      <TableCell className="text-right font-mono">{p.total_engagement?.toLocaleString() || '0'}</TableCell>
                      <TableCell className="text-right font-mono">
                        {p.engagement_rate != null
                          ? `${(p.engagement_rate * 100).toFixed(1)}%`
                          : '0%'}
                      </TableCell>
                      <TableCell className="text-right font-mono">{p.published_count || 0}</TableCell>
                      <TableCell className="text-right font-mono">{p.posts_count || 0}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Top Posts */}
      <Card>
        <CardHeader>
          <CardTitle>Top Performing Posts</CardTitle>
          <CardDescription>Your best content by engagement</CardDescription>
        </CardHeader>
        <CardContent>
          {postsLoading ? (
            <div className="space-y-4">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : topPosts?.length === 0 ? (
            <EmptyState
              icon={BarChart3}
              title="No data for this period"
              description="Publish posts to start seeing engagement metrics. Data appears within 24 hours of publishing."
              primaryAction={{ label: 'Create a post', href: '/content/new', icon: Plus }}
              className="py-8"
            />
          ) : (
            <div className="space-y-3">
              {topPosts?.map((post: TopPost, index: number) => (
                <div
                  key={post.post_id}
                  className="flex items-center justify-between p-3 rounded-lg hover:bg-accent transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-lg font-bold text-muted-foreground/50 w-8 text-right">#{index + 1}</span>
                    <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                      <TrendingUp className="h-5 w-5 text-primary" />
                    </div>
                    <div className="min-w-0">
                      <p className="font-medium truncate">{(post.content_text || 'Untitled').slice(0, 80)}</p>
                      <p className="text-sm text-muted-foreground">
                        {post.platform}
                        {post.published_at ? ` • ${formatRelativeTime(post.published_at)}` : ''}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 text-sm text-muted-foreground">
                    <Badge variant="outline">
                      {(post.engagement ?? 0).toLocaleString()} engagements
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

