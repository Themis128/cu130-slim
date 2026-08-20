'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { TrendingUp, TrendingDown, Minus, Calendar, Download, ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { Skeleton } from '@/components/ui/Skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { Separator } from '@/components/ui/Separator'
import { useOverviewMetrics, usePlatformMetrics, useTopPosts } from '@/hooks/useQueries'
import { formatRelativeTime } from '@/lib/utils'
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
  PieChart,
  Pie,
  Cell,
} from 'recharts'
import { format } from 'date-fns'

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']

export function AnalyticsPage() {
  const [days, setDays] = useState(30)
  const [platformFilter, setPlatformFilter] = useState('')

  const { data: overview, isLoading: overviewLoading } = useOverviewMetrics(days)
  const { data: platformData, isLoading: platformLoading } = usePlatformMetrics(days)
  const { data: topPosts, isLoading: postsLoading } = useTopPosts(10, platformFilter || undefined)

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
      name: 'Total Impressions',
      value: overview?.total_impressions?.toLocaleString() || '0',
      change: overview?.impressions_change || 0,
      icon: TrendingUp,
      color: 'text-blue-500',
    },
    {
      name: 'Total Engagement',
      value: overview?.total_engagement?.toLocaleString() || '0',
      change: overview?.engagement_change || 0,
      icon: TrendingUp,
      color: 'text-green-500',
    },
    {
      name: 'Followers Gained',
      value: overview?.followers_gained?.toLocaleString() || '0',
      change: overview?.followers_change || 0,
      icon: TrendingUp,
      color: 'text-purple-500',
    },
    {
      name: 'Posts Published',
      value: overview?.posts_published?.toLocaleString() || '0',
      change: overview?.posts_change || 0,
      icon: TrendingUp,
      color: 'text-orange-500',
    },
  ]

  const platformMetrics = platformData?.map((p, i) => ({
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
          <Button variant="outline">
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
                <div className={cn('p-3 rounded-full', metric.color + '/10')}>
                  <metric.icon className={cn('h-6 w-6', metric.color)} />
                </div>
              </div>
              <div className="mt-4 flex items-center gap-1 text-sm">
                {metric.change >= 0 ? (
                  <>
                    <TrendingUp className="h-3.5 w-3.5 text-green-500" />
                    <span className="text-green-500">{metric.change.toFixed(1)}%</span>
                    <span className="text-muted-foreground">vs previous period</span>
                  </>
                ) : (
                  <>
                    <TrendingDown className="h-3.5 w-3.5 text-red-500" />
                    <span className="text-red-500">{Math.abs(metric.change).toFixed(1)}%</span>
                    <span className="text-muted-foreground">vs previous period</span>
                  </>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Charts */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Engagement Over Time */}
        <Card>
          <CardHeader>
            <CardTitle>Engagement Over Time</CardTitle>
            <CardDescription>Daily engagement across all platforms</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={overview?.engagement_timeline || []}>
                  <defs>
                    <linearGradient id="colorEngagement" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                  <XAxis dataKey="date" tickFormatter={(v) => format(new Date(v), 'MMM d')} className="text-xs" />
                  <YAxis className="text-xs" />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px' }}
                    formatter={(value: number) => [value.toLocaleString(), 'Engagements']}
                  />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#colorEngagement)"
                  />
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
                  <Bar dataKey="engagement" radius={[0, 4, 4, 0]}>
                    {platformMetrics.map((_, i) => (
                      <Cell key={i} fill={COLORS[i % COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        {/* Impressions Trend */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Impressions Trend</CardTitle>
            <CardDescription>Daily impressions by platform</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={overview?.impressions_timeline || []}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-muted/50" />
                  <XAxis dataKey="date" tickFormatter={(v) => format(new Date(v), 'MMM d')} className="text-xs" />
                  <YAxis className="text-xs" />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: '8px' }}
                    formatter={(value: number) => [value.toLocaleString(), 'Impressions']}
                  />
                  <Line
                    type="monotone"
                    dataKey="linkedin"
                    stroke="#0a66c2"
                    strokeWidth={2}
                    dot={false}
                    name="LinkedIn"
                  />
                  <Line
                    type="monotone"
                    dataKey="twitter"
                    stroke="#1da1f2"
                    strokeWidth={2}
                    dot={false}
                    name="Twitter/X"
                  />
                  <Line
                    type="monotone"
                    dataKey="instagram"
                    stroke="#e4405f"
                    strokeWidth={2}
                    dot={false}
                    name="Instagram"
                  />
                  <Line
                    type="monotone"
                    dataKey="facebook"
                    stroke="#1877f2"
                    strokeWidth={2}
                    dot={false}
                    name="Facebook"
                  />
                </LineChart>
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
                    <TableHead className="text-right">Engagement Rate</TableHead>
                    <TableHead className="text-right">Followers</TableHead>
                    <TableHead className="text-right">Posts</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {platformMetrics.map((p, i) => (
                    <TableRow key={p.platform}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <div className={cn('w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-bold', COLORS[i % COLORS.length])}>
                            {p.platform[0].toUpperCase()}
                          </div>
                          <span className="font-medium capitalize">{p.platform}</span>
                        </div>
                      </TableCell>
                      <TableCell className="text-right font-mono">{p.impressions?.toLocaleString() || '0'}</TableCell>
                      <TableCell className="text-right font-mono">{p.engagement?.toLocaleString() || '0'}</TableCell>
                      <TableCell className="text-right font-mono">
                        {p.engagement_rate ? `${p.engagement_rate.toFixed(2)}%` : '0%'}
                      </TableCell>
                      <TableCell className="text-right font-mono">{p.followers?.toLocaleString() || '0'}</TableCell>
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
            <div className="py-8 text-center text-muted-foreground">
              No posts found for the selected period
            </div>
          ) : (
            <div className="space-y-3">
              {topPosts?.map((post, index) => (
                <div
                  key={post.id}
                  className="flex items-center justify-between p-3 rounded-lg hover:bg-accent transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-lg font-bold text-muted-foreground/50 w-8 text-right">#{index + 1}</span>
                    <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                      <TrendingUp className="h-5 w-5 text-primary" />
                    </div>
                    <div className="min-w-0">
                      <p className="font-medium truncate">{post.content?.slice(0, 80)}...</p>
                      <p className="text-sm text-muted-foreground">
                        {post.platform} • {formatRelativeTime(post.published_at || post.created_at)}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 text-sm text-muted-foreground">
                    <Badge variant="outline">{post.engagement_count?.toLocaleString() || 0} engagements</Badge>
                    <Badge variant={post.status === 'published' ? 'success' : 'secondary'}>{post.status}</Badge>
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

function cn(...classes: (string | undefined | null | false)[]) {
  return classes.filter(Boolean).join(' ')
}