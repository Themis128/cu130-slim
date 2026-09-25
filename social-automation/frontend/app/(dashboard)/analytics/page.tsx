'use client'

import { useState, useMemo } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  TrendingUp, TrendingDown, Download, BarChart3, Plus, ArrowLeftRight,
  Users, Heart, UserCheck, Send, RefreshCw, FileText, Clock, Calendar,
  Bot, Cloud, Zap, AlertTriangle, Globe, Database, HardDrive, Eye,
  Megaphone, MousePointerClick, UserPlus,
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
import { useOverviewMetrics, usePlatformMetrics, useTopPosts, useEngagementTrends, useFollowerGrowth, useLinkedinBestTime, useBotSummary, useCloudflareOverview, usePublishPipeline, useAdCampaigns, useInitiatives } from '@/hooks/useQueries'
import type { PlatformMetrics, TopPost, BotAnalyticsSummary, CloudflareOverview, PublishPipeline, AdCampaignsResponse, GrowthInitiative } from '@/types'
import { formatRelativeTime, cn } from '@/lib/utils'
import { analyticsApi } from '@/services/api'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, AreaChart, Area, Cell, Line,
} from 'recharts'
import { format } from 'date-fns'
import { formatErrorToast } from '@/lib/humanizeError'

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
  const [exporting, setExporting] = useState(false)
  const [bestTime, setBestTime] = useState<{ best_hour?: number; best_day?: string; recommendation?: string; confidence?: string } | null>(null)

  const bestTimeMutation = useLinkedinBestTime()

  const { data: overview, isLoading: overviewLoading, isError: overviewError, refetch: refetchOverview } = useOverviewMetrics(days)
  const { data: platformData, isLoading: platformLoading } = usePlatformMetrics(days)
  const { data: topPosts, isLoading: postsLoading } = useTopPosts(10, platformFilter || undefined, days)
  const { data: rawTrend } = useEngagementTrends(
    compareMode ? days * 2 : days,
    platformFilter || undefined
  )
  const { data: followerData } = useFollowerGrowth(days)
  const { data: botSummaryRaw, isLoading: botLoading } = useBotSummary(days)
  const { data: cfOverviewRaw, isLoading: cfLoading } = useCloudflareOverview(7)
  const { data: pipeline } = usePublishPipeline(days) as { data: PublishPipeline | undefined }
  const { data: adCampaignsRaw } = useAdCampaigns(days)
  const adCampaigns = adCampaignsRaw as AdCampaignsResponse | undefined
  const { data: initiativesRaw } = useInitiatives(90)
  const initiatives = initiativesRaw as GrowthInitiative[] | undefined
  const [inviteBatch, setInviteBatch] = useState('')
  const [inviteCredits, setInviteCredits] = useState('')
  const queryClient = useQueryClient()
  const recordInitiative = useMutation({
    mutationFn: (body: { units: number; credits_left?: number }) =>
      analyticsApi.recordInitiativeEvent({ event_type: 'linkedin_page_invite', platform: 'linkedin', ...body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['analytics', 'initiatives'] })
      setInviteBatch('')
      setInviteCredits('')
      toast.success('Invites logged')
    },
    onError: (err: unknown) => toast.error(formatErrorToast('Couldn’t log invites.', err)),
  })
  const botSummary = botSummaryRaw as BotAnalyticsSummary | undefined
  const cfOverview = cfOverviewRaw as CloudflareOverview | undefined

  const { currentTrend, deltaEngagement } = useMemo(() => {
    const trend = (rawTrend || []) as Array<{ date: string; value: number; impressions?: number; likes?: number; comments?: number; shares?: number; clicks?: number }>
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
    : ((rawTrend || []) as Array<{ date: string; value: number; impressions?: number; likes?: number; comments?: number; shares?: number }>)

  const hasTrendData = engagementTrend.some((d) => (d.value ?? 0) > 0 || (d.impressions ?? 0) > 0)

  const avgEngagement = useMemo(() => {
    const published = overview?.published_posts ?? 0
    const total = overview?.total_engagement ?? 0
    if (published === 0) return 0
    return Math.round(total / published)
  }, [overview])

  // Stacked publish-volume chart: pivot daily rows to {date, <platform>: n}
  const publishDaily = useMemo(() => {
    if (!pipeline?.daily?.length) return []
    const byDate = new Map<string, Record<string, number | string>>()
    for (const d of pipeline.daily) {
      const row = byDate.get(d.date) ?? { date: d.date }
      row[d.platform] = ((row[d.platform] as number) ?? 0) + d.published
      byDate.set(d.date, row)
    }
    return [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)))
  }, [pipeline])
  const dailyPlatforms = useMemo(
    () => [...new Set((pipeline?.daily ?? []).map((d) => d.platform))],
    [pipeline]
  )

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

  if (overviewError) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Analytics</h1>
          <p className="text-muted-foreground mt-1">See what&apos;s working — and what to do next.</p>
        </div>
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <AlertTriangle className="h-10 w-10 text-amber-500" />
            <p className="text-sm font-medium">Couldn&apos;t load analytics overview</p>
            <p className="text-xs text-muted-foreground max-w-md">
              The API request failed. Check that social-api is reachable, then retry.
            </p>
            <Button variant="outline" size="sm" onClick={() => refetchOverview()}>
              <RefreshCw className="mr-1.5 h-4 w-4" />
              Retry
            </Button>
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
      name: 'Impressions',
      value: (overview?.total_impressions ?? 0).toLocaleString(),
      change: null as number | null,
      icon: Eye,
      color: 'text-violet-500',
      bg: 'bg-violet-500/10',
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
          <p className="text-muted-foreground mt-1">See what’s working — and what to do next.</p>
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
          <Select
            value={platformFilter || 'all'}
            onValueChange={(v) => setPlatformFilter(v === 'all' ? '' : v)}
          >
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="All Platforms" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Platforms</SelectItem>
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
                    ? 'Sync queued — analytics will update shortly'
                    : 'Sync started — analytics will update shortly'
                )
              } catch (err: unknown) {
                toast.error(formatErrorToast('Couldn’t start analytics sync. Please try again.', err))
              } finally {
                setSyncing(false)
              }
            }}
          >
            <RefreshCw className={cn('mr-1.5 h-4 w-4', syncing && 'animate-spin')} />
            Sync
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={exporting}
            onClick={async () => {
              setExporting(true)
              try {
                const res = await analyticsApi.exportReport({
                  format: 'csv',
                  days,
                  platform: platformFilter || undefined,
                })
                const blob = new Blob([res.data as BlobPart], { type: 'text/csv' })
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = `analytics_${new Date().toISOString().slice(0, 10)}.csv`
                a.click()
                URL.revokeObjectURL(url)
                toast.success('Export downloaded')
              } catch (err: unknown) {
                toast.error(formatErrorToast('Couldn’t export that report.', err))
              } finally {
                setExporting(false)
              }
            }}
          >
            <Download className="mr-1.5 h-4 w-4" />
            Export
          </Button>
        </div>
      </div>

      {/* Data freshness indicator */}
      {overview?.last_sync_at && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Clock className="h-3.5 w-3.5" />
          <span>Last synced {formatRelativeTime(overview.last_sync_at)}</span>
        </div>
      )}

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

      {/* Publishing Pipeline — live operational status */}
      {pipeline && (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Zap className="h-5 w-5 text-amber-500" />
              <div>
                <CardTitle>Publishing Pipeline</CardTitle>
                <CardDescription>
                  Live delivery status — queue, per-platform success, and upcoming scheduled posts
                </CardDescription>
              </div>
              <span className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-green-500/10 px-2.5 py-1 text-xs font-medium text-green-600 dark:text-green-400">
                <span className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse" />
                Live
              </span>
            </div>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Queue chips */}
            <div className="flex flex-wrap gap-3">
              <div className="rounded-lg border px-4 py-2.5">
                <p className="text-xs text-muted-foreground">Queued</p>
                <p className="text-xl font-bold tabular-nums">{pipeline.queue.pending}</p>
              </div>
              <div className="rounded-lg border px-4 py-2.5">
                <p className="text-xs text-muted-foreground">Publishing now</p>
                <p className="text-xl font-bold tabular-nums text-blue-500">{pipeline.queue.processing}</p>
              </div>
              <div className="rounded-lg border px-4 py-2.5">
                <p className="text-xs text-muted-foreground">Stuck</p>
                <p className={cn('text-xl font-bold tabular-nums', pipeline.queue.stuck_processing > 0 ? 'text-red-500' : '')}>
                  {pipeline.queue.stuck_processing}
                </p>
              </div>
              <div className="rounded-lg border px-4 py-2.5">
                <p className="text-xs text-muted-foreground">Published ({days}d)</p>
                <p className="text-xl font-bold tabular-nums text-green-500">{pipeline.queue.published_period}</p>
              </div>
              <div className="rounded-lg border px-4 py-2.5">
                <p className="text-xs text-muted-foreground">Failed ({days}d)</p>
                <p className={cn('text-xl font-bold tabular-nums', pipeline.queue.failed_period > 0 ? 'text-red-500' : '')}>
                  {pipeline.queue.failed_period}
                </p>
              </div>
            </div>

            {/* Daily publish volume — stacked by platform */}
            {publishDaily.length > 0 && (
              <div>
                <p className="text-sm font-medium mb-2">Posts published per day</p>
                <div style={{ height: '12rem' }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={publishDaily}>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                      <XAxis
                        dataKey="date"
                        tickFormatter={(v) => { try { return format(new Date(v as string), 'MMM d') } catch { return v as string } }}
                        className="text-xs"
                      />
                      <YAxis className="text-xs" allowDecimals={false} />
                      <Tooltip
                        contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px', fontSize: 12 }}
                        labelFormatter={(label: string) => { try { return format(new Date(label), 'MMM d, yyyy') } catch { return label } }}
                      />
                      {dailyPlatforms.map((plat, i) => (
                        <Bar
                          key={plat}
                          dataKey={plat}
                          stackId="published"
                          name={plat}
                          fill={PLATFORM_COLOR[plat] ?? COLORS[i % COLORS.length]}
                          radius={i === dailyPlatforms.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
                        />
                      ))}
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            {/* Per-platform delivery table */}
            {pipeline.platforms.length > 0 && (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Platform</TableHead>
                      <TableHead className="text-right">Success</TableHead>
                      <TableHead className="text-right">Published</TableHead>
                      <TableHead className="text-right">Failed</TableHead>
                      <TableHead className="text-right">Pending</TableHead>
                      <TableHead>Last published</TableHead>
                      <TableHead>Last error</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pipeline.platforms.map((p) => (
                      <TableRow key={p.platform}>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <div
                              className="w-6 h-6 rounded-full flex items-center justify-center text-white text-[10px] font-bold shrink-0"
                              style={{ backgroundColor: PLATFORM_COLOR[p.platform] ?? '#6366f1' }}
                            >
                              {p.platform[0].toUpperCase()}
                            </div>
                            <span className="font-medium capitalize">{p.platform}</span>
                          </div>
                        </TableCell>
                        <TableCell className="text-right">
                          {p.success_rate != null ? (
                            <span className={cn(
                              'inline-flex items-center gap-2 font-mono text-sm font-medium',
                              p.success_rate >= 0.9 ? 'text-green-500' : p.success_rate >= 0.7 ? 'text-amber-500' : 'text-red-500'
                            )}>
                              <span className="inline-block h-1.5 w-16 rounded-full bg-muted overflow-hidden">
                                <span
                                  className="block h-full rounded-full"
                                  style={{
                                    width: `${Math.round(p.success_rate * 100)}%`,
                                    backgroundColor: p.success_rate >= 0.9 ? '#22c55e' : p.success_rate >= 0.7 ? '#f59e0b' : '#ef4444',
                                  }}
                                />
                              </span>
                              {Math.round(p.success_rate * 100)}%
                            </span>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">{p.published}</TableCell>
                        <TableCell className={cn('text-right font-mono text-sm', p.failed > 0 && 'text-red-500 font-medium')}>
                          {p.failed}
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">{p.pending}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {p.last_published_at ? formatRelativeTime(p.last_published_at) : '—'}
                        </TableCell>
                        <TableCell className="max-w-[260px]">
                          {p.last_error ? (
                            <span className="text-xs text-red-500 line-clamp-2" title={p.last_error}>
                              {p.last_error}
                            </span>
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}

            {/* Account health + upcoming schedule */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div>
                <p className="text-sm font-medium mb-2">Connected accounts</p>
                {pipeline.accounts.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No accounts connected.</p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {pipeline.accounts.map((a, i) => {
                      const expired = a.token_expires_at ? new Date(a.token_expires_at) < new Date() : false
                      const expiringSoon = a.token_expires_at
                        ? !expired && new Date(a.token_expires_at).getTime() - Date.now() < 7 * 864e5
                        : false
                      const dot = a.status !== 'active' || expired
                        ? 'bg-red-500'
                        : expiringSoon
                          ? 'bg-amber-500'
                          : 'bg-green-500'
                      return (
                        <span
                          key={`${a.platform}-${a.username}-${i}`}
                          className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs"
                          title={`${a.status}${expired ? ' — token expired' : expiringSoon ? ' — token expiring soon' : ''}`}
                        >
                          <span className={cn('h-1.5 w-1.5 rounded-full', dot)} />
                          <span className="capitalize">{a.platform}</span>
                          {a.username && <span className="text-muted-foreground">@{a.username}</span>}
                        </span>
                      )
                    })}
                  </div>
                )}
              </div>
              <div>
                <p className="text-sm font-medium mb-2">Upcoming scheduled</p>
                {pipeline.upcoming.length === 0 ? (
                  <p className="text-xs text-muted-foreground">Nothing scheduled.</p>
                ) : (
                  <div className="space-y-1.5">
                    {pipeline.upcoming.map((u) => (
                      <Link
                        key={u.post_id}
                        href={`/content/${u.post_id}/edit`}
                        className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2 hover:bg-accent transition-colors"
                      >
                        <span className="text-xs truncate min-w-0">{u.content_preview || 'Untitled'}</span>
                        <span className="flex items-center gap-1.5 shrink-0">
                          {u.platforms.map((pl) => (
                            <span
                              key={pl}
                              className="inline-block h-2 w-2 rounded-full"
                              style={{ backgroundColor: PLATFORM_COLOR[pl] ?? '#6366f1' }}
                              title={pl}
                            />
                          ))}
                          <span className="text-xs text-muted-foreground tabular-nums">
                            {u.scheduled_at ? formatRelativeTime(u.scheduled_at) : ''}
                          </span>
                        </span>
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* LinkedIn Ads — campaign metrics from daily Campaign Manager scrape */}
      {adCampaigns && adCampaigns.campaigns.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Megaphone className="h-5 w-5 text-[#0077b5]" />
              LinkedIn Ads
            </CardTitle>
            <CardDescription>
              {adCampaigns.totals.campaigns} campaign{adCampaigns.totals.campaigns > 1 ? 's' : ''} — daily Campaign Manager snapshots
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* KPI strip */}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
              {[
                { name: 'Spend', value: `€${adCampaigns.totals.spend_eur.toLocaleString(undefined, { maximumFractionDigits: 2 })}`, icon: TrendingUp },
                { name: 'Impressions', value: adCampaigns.totals.impressions.toLocaleString(), icon: Eye },
                { name: 'Clicks', value: adCampaigns.totals.clicks.toLocaleString(), icon: MousePointerClick },
                { name: 'CTR', value: `${adCampaigns.totals.ctr.toFixed(2)}%`, icon: BarChart3 },
                { name: 'CPC', value: `€${adCampaigns.totals.cpc_eur.toFixed(2)}`, icon: TrendingDown },
                { name: 'Engagements', value: adCampaigns.totals.engagements.toLocaleString(), icon: Heart },
              ].map((kpi) => (
                <div key={kpi.name} className="rounded-lg border bg-card p-3">
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <kpi.icon className="h-3.5 w-3.5" />
                    {kpi.name}
                  </div>
                  <div className="mt-1 text-lg font-semibold">{kpi.value}</div>
                </div>
              ))}
            </div>

            {/* Spend + reach charts */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div>
                <p className="text-sm font-medium mb-2">Cumulative spend (€)</p>
                <div style={{ height: '14rem' }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={(() => {
                      const byDay = new Map<string, number>()
                      for (const c of adCampaigns.campaigns) {
                        const perDay = new Map<string, number>()
                        for (const p of c.series) {
                          const d = p.captured_at.slice(0, 10)
                          perDay.set(d, Math.max(perDay.get(d) ?? 0, p.spend_eur))
                        }
                        for (const [d, v] of perDay) byDay.set(d, (byDay.get(d) ?? 0) + v)
                      }
                      return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, spend]) => ({ date, spend: Math.round(spend * 100) / 100 }))
                    })()}>
                      <defs>
                        <linearGradient id="spendGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#0077b5" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#0077b5" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                      <XAxis dataKey="date" tickFormatter={(v) => { try { return format(new Date(v as string), 'MMM d') } catch { return v as string } }} className="text-xs" />
                      <YAxis className="text-xs" />
                      <Tooltip
                        contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px', fontSize: 12 }}
                        formatter={(value: number) => [`€${Number(value).toFixed(2)}`, 'Spend']}
                        labelFormatter={(label: string) => { try { return format(new Date(label), 'MMM d, yyyy') } catch { return label } }}
                      />
                      <Area type="monotone" dataKey="spend" stroke="#0077b5" strokeWidth={2} fillOpacity={1} fill="url(#spendGrad)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
              <div>
                <p className="text-sm font-medium mb-2">Impressions &amp; clicks</p>
                <div style={{ height: '14rem' }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={(() => {
                      const byDay = new Map<string, { impressions: number; clicks: number }>()
                      for (const c of adCampaigns.campaigns) {
                        const perDay = new Map<string, { impressions: number; clicks: number }>()
                        for (const p of c.series) {
                          const d = p.captured_at.slice(0, 10)
                          const cur = perDay.get(d) ?? { impressions: 0, clicks: 0 }
                          perDay.set(d, { impressions: Math.max(cur.impressions, p.impressions), clicks: Math.max(cur.clicks, p.clicks) })
                        }
                        for (const [d, v] of perDay) {
                          const cur = byDay.get(d) ?? { impressions: 0, clicks: 0 }
                          byDay.set(d, { impressions: cur.impressions + v.impressions, clicks: cur.clicks + v.clicks })
                        }
                      }
                      return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, v]) => ({ date, ...v }))
                    })()}>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                      <XAxis dataKey="date" tickFormatter={(v) => { try { return format(new Date(v as string), 'MMM d') } catch { return v as string } }} className="text-xs" />
                      <YAxis className="text-xs" allowDecimals={false} />
                      <Tooltip
                        contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px', fontSize: 12 }}
                        formatter={(value: number, name: string) => [Number(value).toLocaleString(), name === 'impressions' ? 'Impressions' : 'Clicks']}
                        labelFormatter={(label: string) => { try { return format(new Date(label), 'MMM d, yyyy') } catch { return label } }}
                      />
                      <Area type="monotone" dataKey="impressions" stroke="#8b5cf6" strokeWidth={1.5} fill="#8b5cf6" fillOpacity={0.15} name="impressions" />
                      <Area type="monotone" dataKey="clicks" stroke="#10b981" strokeWidth={2} fill="#10b981" fillOpacity={0.15} name="clicks" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>

            {/* Campaign table */}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Campaign</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Budget</TableHead>
                  <TableHead className="text-right">Impressions</TableHead>
                  <TableHead className="text-right">Clicks</TableHead>
                  <TableHead className="text-right">CTR</TableHead>
                  <TableHead className="text-right">CPC</TableHead>
                  <TableHead className="text-right">Engagements</TableHead>
                  <TableHead>Last snapshot</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {adCampaigns.campaigns.map((c) => {
                  const latest = c.latest
                  const peak = c.series.reduce(
                    (m, p) => ({
                      spend_eur: Math.max(m.spend_eur, p.spend_eur),
                      impressions: Math.max(m.impressions, p.impressions),
                      clicks: Math.max(m.clicks, p.clicks),
                      engagements: Math.max(m.engagements, p.engagements),
                    }),
                    { spend_eur: 0, impressions: 0, clicks: 0, engagements: 0 },
                  )
                  const budget = latest?.budget_eur ?? 0
                  const pct = budget > 0 ? Math.min(100, (peak.spend_eur / budget) * 100) : null
                  return (
                    <TableRow key={c.campaign_id}>
                      <TableCell>
                        <div className="font-medium">{c.campaign_name || c.campaign_id}</div>
                        <div className="text-xs text-muted-foreground">{c.campaign_id}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={c.status === 'active' ? 'success' : c.status === 'paused' ? 'warning' : 'secondary'} className="capitalize">
                          {c.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="text-sm">€{peak.spend_eur.toFixed(2)}{budget > 0 && ` / €${budget.toFixed(2)}`}</div>
                        {pct !== null && (
                          <div className="mt-1 h-1.5 w-24 rounded-full bg-muted overflow-hidden">
                            <div
                              className={cn('h-full rounded-full', pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-amber-500' : 'bg-[#0077b5]')}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="text-right">{peak.impressions.toLocaleString()}</TableCell>
                      <TableCell className="text-right">{peak.clicks.toLocaleString()}</TableCell>
                      <TableCell className="text-right">{peak.impressions > 0 ? `${((peak.clicks / peak.impressions) * 100).toFixed(2)}%` : '—'}</TableCell>
                      <TableCell className="text-right">{peak.clicks > 0 ? `€${(peak.spend_eur / peak.clicks).toFixed(2)}` : '—'}</TableCell>
                      <TableCell className="text-right">{peak.engagements.toLocaleString()}</TableCell>
                      <TableCell className="text-muted-foreground text-sm">{latest ? formatRelativeTime(latest.captured_at) : '—'}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Growth initiatives — off-platform pushes (e.g. LinkedIn Page invites) vs follower delta */}
      {initiatives && initiatives.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <UserPlus className="h-5 w-5 text-[#0077b5]" />
              Growth initiatives
            </CardTitle>
            <CardDescription>
              LinkedIn gives every accepted invite its credit back (within ~72h) — high
              acceptance means you can invite more people this month. Credits renew on the 1st.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {initiatives.map((i) => (
              <div
                key={`${i.event_type}-${i.platform}`}
                className="rounded-lg border p-4 space-y-3"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{i.initiative}</span>
                  <span className="text-xs text-muted-foreground">
                    {i.last_at ? `last logged ${formatRelativeTime(i.last_at)}` : ''}
                  </span>
                </div>

                {/* Invite funnel: sent → accepted / waiting / declined */}
                <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
                  <span>
                    <b>{i.units.toLocaleString()}</b> invites sent
                    {i.units_this_month !== i.units && (
                      <span className="text-muted-foreground"> ({i.units_this_month} this month)</span>
                    )}
                  </span>
                  <span className="text-muted-foreground">→</span>
                  {i.accepted_est !== null && (
                    <span><b className="text-emerald-500">{i.accepted_est}</b> accepted</span>
                  )}
                  {i.pending_est !== null && i.pending_est > 0 && (
                    <span><b>{i.pending_est}</b> waiting for a reply</span>
                  )}
                  {i.declined > 0 && (
                    <span><b className="text-red-400">{i.declined}</b> declined</span>
                  )}
                  {i.conversion_pct !== null && (
                    <Badge variant="outline">{i.conversion_pct}% acceptance</Badge>
                  )}
                </div>

                {/* Follower outcome + credit balance */}
                <div className="flex flex-wrap items-center gap-x-8 gap-y-2 text-sm">
                  {i.followers_start !== null && (
                    <span className="text-muted-foreground">
                      Page followers: {i.followers_start} →{' '}
                      <b className="text-foreground">{i.followers_now}</b>
                      {i.followers_delta !== null && i.followers_delta !== 0 && (
                        <span className="text-emerald-500"> ({i.followers_delta > 0 ? '+' : ''}{i.followers_delta})</span>
                      )}
                    </span>
                  )}
                  {i.credits_left !== null && (
                    <span className="flex items-center gap-2 text-muted-foreground">
                      Credits: <b className="text-foreground">~{i.credits_left}</b> of{' '}
                      {i.monthly_cap} left
                      <span className="inline-block h-2 w-24 rounded-full bg-muted overflow-hidden">
                        <span
                          className="block h-full rounded-full bg-emerald-500"
                          style={{ width: `${Math.round((i.credits_left / i.monthly_cap) * 100)}%` }}
                        />
                      </span>
                    </span>
                  )}
                </div>
              </div>
            ))}

            {/* Log an invite batch — sends happen on LinkedIn, we track the count */}
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <input
                type="number"
                min={1}
                placeholder="Invites sent"
                value={inviteBatch}
                onChange={(e) => setInviteBatch(e.target.value)}
                className="h-9 w-32 rounded-md border bg-background px-3 text-sm"
              />
              <input
                type="number"
                min={0}
                placeholder="Credits left (optional)"
                value={inviteCredits}
                onChange={(e) => setInviteCredits(e.target.value)}
                className="h-9 w-40 rounded-md border bg-background px-3 text-sm"
                title="The balance LinkedIn shows at the top of the Invite connections window — the most accurate credit reading"
              />
              <Button
                variant="outline"
                size="sm"
                disabled={recordInitiative.isPending || !inviteBatch || Number(inviteBatch) <= 0}
                onClick={() =>
                  recordInitiative.mutate({
                    units: Number(inviteBatch),
                    credits_left: inviteCredits ? Number(inviteCredits) : undefined,
                  })
                }
              >
                {recordInitiative.isPending ? 'Logging…' : 'Log LinkedIn invites'}
              </Button>
              <span className="text-xs text-muted-foreground">
                Invite connections on LinkedIn, then log the count — optionally the credit balance LinkedIn shows.
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Charts — 2-col on wide screens, 1-col on narrow */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '1.5rem' }}>
        {/* Engagement Over Time */}
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between">
              <div>
                <CardTitle>Engagement Over Time</CardTitle>
                <CardDescription>
                  Daily impressions and engagements
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
                      <linearGradient id="impGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.15} />
                        <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
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
                        name === 'prev' ? 'Prev period' : name === 'impressions' ? 'Impressions' : name === 'value' ? 'Engagement' : name,
                      ]}
                      labelFormatter={(label: string) => { try { return format(new Date(label), 'MMM d, yyyy') } catch { return label } }}
                    />
                    <Area type="monotone" dataKey="impressions" stroke="#8b5cf6" strokeWidth={1.5} fillOpacity={1} fill="url(#impGrad)" name="impressions" />
                    <Area type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={2} fillOpacity={1} fill="url(#engGrad)" name="value" />
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
            <CardDescription>Impressions and engagement per platform</CardDescription>
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
                      formatter={(value: number, name: string) => [
                        value.toLocaleString(),
                        name === 'total_impressions' ? 'Impressions' : 'Engagements',
                      ]}
                    />
                    <Bar dataKey="total_impressions" radius={[0, 4, 4, 0]} fill="#8b5cf6" fillOpacity={0.4} name="total_impressions" />
                    <Bar dataKey="total_engagement" radius={[0, 4, 4, 0]} name="total_engagement">
                      {platformMetrics.map((p, i) => (
                        <Cell key={`eng-${p.platform}-${i}`} fill={p.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Follower Growth — full width time series */}
        <Card style={{ gridColumn: '1 / -1' }}>
          <CardHeader>
            <CardTitle>Follower Growth</CardTitle>
            <CardDescription>
              Follower count over time per platform
              {followerData && followerData.length > 0 && ` — ${followerData.length} platform${followerData.length > 1 ? 's' : ''}`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!followerData || followerData.length === 0 ? (
              <div style={{ height: '18rem', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', gap: '0.5rem' }}>
                <UserCheck className="h-10 w-10 text-muted-foreground/30" />
                <p className="text-sm font-medium text-muted-foreground">No follower data yet</p>
                <p className="text-xs text-muted-foreground/70">Run a sync to start tracking follower growth over time.</p>
              </div>
            ) : (
              <div style={{ height: '18rem' }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart>
                    <defs>
                      {followerData.map((fp, i) => (
                        <linearGradient key={`grad-${fp.platform}-${i}`} id={`folGrad-${fp.platform}-${i}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={PLATFORM_COLOR[fp.platform] ?? COLORS[i % COLORS.length]} stopOpacity={0.25} />
                          <stop offset="95%" stopColor={PLATFORM_COLOR[fp.platform] ?? COLORS[i % COLORS.length]} stopOpacity={0} />
                        </linearGradient>
                      ))}
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                    <XAxis
                      dataKey="date"
                      tickFormatter={(v) => { try { return format(new Date(v as string), 'MMM d') } catch { return v as string } }}
                      className="text-xs"
                      allowDuplicatedCategory={false}
                    />
                    <YAxis className="text-xs" allowDecimals={false} />
                    <Tooltip
                      contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px', fontSize: 12 }}
                      formatter={(value: number, name: string) => [value.toLocaleString(), name]}
                      labelFormatter={(label: string) => { try { return format(new Date(label), 'MMM d, yyyy') } catch { return label } }}
                    />
                    {followerData.map((fp, i) => (
                      <Area
                        key={`area-${fp.platform}-${i}`}
                        type="monotone"
                        dataKey="followers"
                        data={fp.series.map((s) => ({ date: s.date, followers: s.followers, platform: fp.platform }))}
                        name={fp.platform}
                        stroke={PLATFORM_COLOR[fp.platform] ?? COLORS[i % COLORS.length]}
                        strokeWidth={2}
                        fillOpacity={1}
                        fill={`url(#folGrad-${fp.platform}-${i})`}
                      />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
                {/* Follower summary chips */}
                <div className="flex flex-wrap gap-3 mt-3">
                  {followerData.map((fp, i) => (
                    <div key={`chip-${fp.platform}-${i}`} className="flex items-center gap-2 text-xs">
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-full"
                        style={{ backgroundColor: PLATFORM_COLOR[fp.platform] ?? COLORS[i % COLORS.length] }}
                      />
                      <span className="font-medium capitalize">{fp.platform}</span>
                      <span className="tabular-nums text-muted-foreground">{fp.current.toLocaleString()}</span>
                      {fp.change !== 0 && (
                        <span className={cn('tabular-nums', fp.change > 0 ? 'text-green-500' : 'text-red-500')}>
                          {fp.change > 0 ? '+' : ''}{fp.change.toLocaleString()}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
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
                        <Cell key={`imp-${p.platform}-${i}`} fill={p.color} />
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
                    {platformMetrics.map((p, i) => (
                      <TableRow key={`row-${p.platform}-${i}`}>
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

      {/* Best Time to Post */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between">
          <div>
            <CardTitle>Best Time to Post</CardTitle>
            <CardDescription>
              Recommended posting times based on your engagement analytics
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={bestTimeMutation.isPending}
            onClick={async () => {
              try {
                const res = await bestTimeMutation.mutateAsync({ account_type: 'organization' })
                setBestTime(res.data as typeof bestTime)
              } catch (err: unknown) {
                toast.error(formatErrorToast('Couldn’t load recommendations right now.', err))
              }
            }}
          >
            <Calendar className="mr-1.5 h-4 w-4" />
            {bestTimeMutation.isPending ? 'Analyzing…' : 'Get Recommendation'}
          </Button>
        </CardHeader>
        <CardContent>
          {!bestTime ? (
            <div className="flex flex-col items-center justify-center py-8 text-center gap-2">
              <Calendar className="h-8 w-8 text-muted-foreground/30" />
              <p className="text-sm text-muted-foreground">
                Click “Get Recommendation” to analyze your engagement history and suggest a posting window.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {bestTime.best_hour != null && (
                <div className="rounded-lg border p-4 text-center">
                  <p className="text-xs text-muted-foreground mb-1">Best Hour</p>
                  <p className="text-2xl font-bold tabular-nums">{bestTime.best_hour}:00</p>
                </div>
              )}
              {bestTime.best_day && (
                <div className="rounded-lg border p-4 text-center">
                  <p className="text-xs text-muted-foreground mb-1">Best Day</p>
                  <p className="text-2xl font-bold capitalize">{bestTime.best_day}</p>
                </div>
              )}
              {bestTime.confidence && (
                <div className="rounded-lg border p-4 text-center">
                  <p className="text-xs text-muted-foreground mb-1">Confidence</p>
                  <p className="text-2xl font-bold capitalize">{bestTime.confidence}</p>
                </div>
              )}
              {bestTime.recommendation && (
                <div className="sm:col-span-3 rounded-lg border p-4">
                  <p className="text-xs text-muted-foreground mb-1">Recommendation</p>
                  <p className="text-sm">{bestTime.recommendation}</p>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

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

      {/* Bot Analytics Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-blue-500" />
            <div>
              <CardTitle>Bot Reply Analytics</CardTitle>
              <CardDescription>
                Auto-reply bot performance, guardrails, and provider fallback
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {botLoading ? (
            <div className="space-y-3">
              {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
            </div>
          ) : !botSummary || botSummary.total_replies === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-center gap-2">
              <Bot className="h-8 w-8 text-muted-foreground/30" />
              <p className="text-sm text-muted-foreground">
                No bot replies yet. Replies appear here after the bot responds to messages.
              </p>
            </div>
          ) : (
            <div className="space-y-6">
              {/* Bot KPI Cards */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '1rem' }}>
                <div className="rounded-lg border p-4">
                  <p className="text-xs text-muted-foreground mb-1">Total Replies</p>
                  <p className="text-2xl font-bold tabular-nums">{botSummary.total_replies}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <p className="text-xs text-muted-foreground mb-1">Success Rate</p>
                  <p className="text-2xl font-bold tabular-nums text-green-600">
                    {botSummary.total_replies > 0
                      ? `${((botSummary.successful_replies / botSummary.total_replies) * 100).toFixed(0)}%`
                      : '—'}
                  </p>
                </div>
                <div className="rounded-lg border p-4">
                  <p className="text-xs text-muted-foreground mb-1">Guardrail Triggers</p>
                  <p className="text-2xl font-bold tabular-nums text-amber-600">{botSummary.guardrail_triggers}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <p className="text-xs text-muted-foreground mb-1">Pricing Guardrails</p>
                  <p className="text-2xl font-bold tabular-nums text-orange-600">{botSummary.pricing_guardrail_triggers}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <p className="text-xs text-muted-foreground mb-1">Greek Replies</p>
                  <p className="text-2xl font-bold tabular-nums">{botSummary.greek_replies}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <p className="text-xs text-muted-foreground mb-1">English Replies</p>
                  <p className="text-2xl font-bold tabular-nums">{botSummary.english_replies}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <p className="text-xs text-muted-foreground mb-1">Avg Latency</p>
                  <p className="text-2xl font-bold tabular-nums">
                    {botSummary.avg_latency_ms != null ? `${botSummary.avg_latency_ms}ms` : '—'}
                  </p>
                </div>
                <div className="rounded-lg border p-4">
                  <p className="text-xs text-muted-foreground mb-1">Failed Replies</p>
                  <p className="text-2xl font-bold tabular-nums text-red-600">{botSummary.failed_replies}</p>
                </div>
              </div>

              {/* Provider Breakdown */}
              {botSummary.by_provider.length > 0 && (
                <div>
                  <p className="text-sm font-medium mb-3">Provider Breakdown</p>
                  <div className="space-y-2">
                    {botSummary.by_provider.map((p) => (
                      <div key={p.provider} className="flex items-center justify-between rounded-lg border p-3">
                        <div className="flex items-center gap-2">
                          {p.provider === 'cloudflare' && <Cloud className="h-4 w-4 text-orange-500" />}
                          {p.provider === 'dmr' && <HardDrive className="h-4 w-4 text-blue-500" />}
                          {p.provider === 'deterministic' && <Zap className="h-4 w-4 text-amber-500" />}
                          <span className="font-medium capitalize">{p.provider}</span>
                        </div>
                        <div className="flex items-center gap-4 text-sm">
                          <span className="tabular-nums text-green-600">{p.replies} replies</span>
                          {p.errors > 0 && (
                            <span className="tabular-nums text-red-600">{p.errors} errors</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Daily Bot Activity Chart */}
              {botSummary.by_day.length > 0 && (
                <div>
                  <p className="text-sm font-medium mb-3">Daily Bot Activity</p>
                  <div style={{ height: '12rem' }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={botSummary.by_day}>
                        <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                        <XAxis
                          dataKey="date"
                          tickFormatter={(v) => { try { return format(new Date(v as string), 'MMM d') } catch { return v as string } }}
                          className="text-xs"
                        />
                        <YAxis className="text-xs" allowDecimals={false} />
                        <Tooltip
                          contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px', fontSize: 12 }}
                          labelFormatter={(label: string) => { try { return format(new Date(label), 'MMM d, yyyy') } catch { return label } }}
                        />
                        <Bar dataKey="replies" fill="#3b82f6" radius={[4, 4, 0, 0]} name="Replies" />
                        <Bar dataKey="errors" fill="#ef4444" radius={[4, 4, 0, 0]} name="Errors" />
                        <Bar dataKey="guardrails" fill="#f59e0b" radius={[4, 4, 0, 0]} name="Guardrails" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Cloudflare Analytics Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Cloud className="h-5 w-5 text-orange-500" />
            <div>
              <CardTitle>Cloudflare Infrastructure Analytics</CardTitle>
              <CardDescription>
                Workers AI, Workers, R2, D1, KV, and Vectorize usage (free GraphQL API)
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {cfLoading ? (
            <div className="space-y-3">
              {[1, 2, 3, 4, 5, 6].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
            </div>
          ) : !cfOverview ? (
            <div className="flex flex-col items-center justify-center py-8 text-center gap-2">
              <Cloud className="h-8 w-8 text-muted-foreground/30" />
              <p className="text-sm text-muted-foreground">
                Cloudflare analytics unavailable. Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID.
              </p>
            </div>
          ) : (
            <div className="space-y-6">
              {/* Workers AI KPIs */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '1rem' }}>
                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Zap className="h-3.5 w-3.5 text-orange-500" />
                    <p className="text-xs text-muted-foreground">AI Requests</p>
                  </div>
                  <p className="text-2xl font-bold tabular-nums">{cfOverview.workers_ai.total_requests.toLocaleString()}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Zap className="h-3.5 w-3.5 text-orange-500" />
                    <p className="text-xs text-muted-foreground">Neurons Used</p>
                  </div>
                  <p className="text-2xl font-bold tabular-nums">{cfOverview.workers_ai.total_neurons.toLocaleString()}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Cloud className="h-3.5 w-3.5 text-green-500" />
                    <p className="text-xs text-muted-foreground">Free Remaining</p>
                  </div>
                  <p className="text-2xl font-bold tabular-nums text-green-600">
                    {cfOverview.workers_ai.free_tier_remaining.toLocaleString()}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    of {cfOverview.workers_ai.free_tier_limit.toLocaleString()} / 7d
                  </p>
                </div>
                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Globe className="h-3.5 w-3.5 text-blue-500" />
                    <p className="text-xs text-muted-foreground">Worker Requests</p>
                  </div>
                  <p className="text-2xl font-bold tabular-nums">{cfOverview.workers.total_requests.toLocaleString()}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-1.5 mb-1">
                    <AlertTriangle className="h-3.5 w-3.5 text-red-500" />
                    <p className="text-xs text-muted-foreground">Worker Errors</p>
                  </div>
                  <p className="text-2xl font-bold tabular-nums text-red-600">{cfOverview.workers.total_errors.toLocaleString()}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-1.5 mb-1">
                    <HardDrive className="h-3.5 w-3.5 text-purple-500" />
                    <p className="text-xs text-muted-foreground">R2 Operations</p>
                  </div>
                  <p className="text-2xl font-bold tabular-nums">{cfOverview.r2.total_operations.toLocaleString()}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Database className="h-3.5 w-3.5 text-indigo-500" />
                    <p className="text-xs text-muted-foreground">D1 Queries</p>
                  </div>
                  <p className="text-2xl font-bold tabular-nums">{cfOverview.d1.total_queries.toLocaleString()}</p>
                </div>
                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Database className="h-3.5 w-3.5 text-cyan-500" />
                    <p className="text-xs text-muted-foreground">KV Operations</p>
                  </div>
                  <p className="text-2xl font-bold tabular-nums">{cfOverview.kv.total_operations.toLocaleString()}</p>
                </div>
              </div>

              {/* Workers AI Model Breakdown */}
              {cfOverview.workers_ai.by_model.length > 0 && (
                <div>
                  <p className="text-sm font-medium mb-3">Workers AI Model Breakdown</p>
                  <div className="space-y-2">
                    {cfOverview.workers_ai.by_model.slice(0, 5).map((m) => (
                      <div key={m.model} className="flex items-center justify-between rounded-lg border p-3">
                        <span className="font-mono text-xs truncate max-w-[60%]">{m.model}</span>
                        <div className="flex items-center gap-4 text-sm">
                          <span className="tabular-nums">{m.requests} req</span>
                          <span className="tabular-nums text-orange-600">{m.neurons} neurons</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Workers Script Breakdown */}
              {cfOverview.workers.by_script.length > 0 && (
                <div>
                  <p className="text-sm font-medium mb-3">Worker Scripts</p>
                  <div className="space-y-2">
                    {cfOverview.workers.by_script.map((s) => (
                      <div key={s.script} className="flex items-center justify-between rounded-lg border p-3">
                        <span className="font-mono text-xs truncate max-w-[50%]">{s.script}</span>
                        <div className="flex items-center gap-4 text-sm">
                          <span className="tabular-nums">{s.requests.toLocaleString()} req</span>
                          {s.errors > 0 && (
                            <span className="tabular-nums text-red-600">{s.errors} errors</span>
                          )}
                          <span className="tabular-nums text-muted-foreground">p50: {s.cpu_time_p50}μs</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* R2 Bucket Breakdown */}
              {cfOverview.r2.by_bucket.length > 0 && (
                <div>
                  <p className="text-sm font-medium mb-3">R2 Buckets</p>
                  <div className="space-y-2">
                    {cfOverview.r2.by_bucket.map((b) => (
                      <div key={b.bucket} className="flex items-center justify-between rounded-lg border p-3">
                        <span className="font-mono text-xs">{b.bucket}</span>
                        <span className="tabular-nums text-sm">{b.operations.toLocaleString()} ops</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* D1 Database Breakdown */}
              {cfOverview.d1.by_database.length > 0 && (
                <div>
                  <p className="text-sm font-medium mb-3">D1 Databases</p>
                  <div className="space-y-2">
                    {cfOverview.d1.by_database.map((d) => (
                      <div key={d.database} className="flex items-center justify-between rounded-lg border p-3">
                        <span className="font-mono text-xs truncate max-w-[60%]">{d.database}</span>
                        <span className="tabular-nums text-sm">{d.queries.toLocaleString()} queries</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
