'use client'

import { useEffect } from 'react'
import {
  TrendingUp, Users, FileText, Clock, ExternalLink, Plus,
  Sparkles, AlertCircle, PenLine, Image, BarChart2, Calendar,
  Send,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { WeekCalendar } from '@/components/ui/WeekCalendar'
import { PostingHeatmap } from '@/components/ui/PostingHeatmap'
import { useOverviewMetrics, useTopPosts, useScheduledPosts } from '@/hooks/useQueries'
import { useAdvisor } from '@/hooks/useAdvisor'
import { useAuth } from '@/hooks/useAuth'
import type { TopPost } from '@/types'
import { formatRelativeTime, cn } from '@/lib/utils'
import Link from 'next/link'

function sanitizePostContent(text: string | null | undefined): string {
  if (!text) return 'Untitled'
  if (/^urn:li:/i.test(text.trim())) return 'LinkedIn post'
  return text
}

function greeting(name: string | null): string {
  const hour = new Date().getHours()
  const first = name?.trim().split(' ')[0] ?? null
  const salutation = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  return first ? `${salutation}, ${first}` : salutation
}

const PLATFORM_COLOR: Record<string, string> = {
  linkedin: '#0077b5',
  twitter: '#1da1f2',
  instagram: '#e1306c',
  facebook: '#1877f2',
  threads: '#000000',
  tiktok: '#010101',
}

export default function DashboardPage() {
  const { data: metrics, isLoading: metricsLoading } = useOverviewMetrics(30)
  const { data: topPosts, isLoading: postsLoading } = useTopPosts(5)
  const { data: scheduledPosts = [] } = useScheduledPosts()
  const { setCtx } = useAdvisor()
  const { user } = useAuth()

  useEffect(() => {
    if (!metrics) return
    setCtx({
      connectedAccountsCount: metrics.connected_accounts ?? 0,
      publishedCount: metrics.published_posts ?? 0,
      scheduledCount: metrics.scheduled_posts ?? 0,
    })
  }, [metrics, setCtx])

  const stats = [
    {
      name: 'Published',
      value: metrics?.published_posts ?? 0,
      icon: TrendingUp,
      color: 'text-green-500',
      bg: 'bg-green-500/10',
      href: '/analytics',
    },
    {
      name: 'Scheduled',
      value: metrics?.scheduled_posts ?? 0,
      icon: Clock,
      color: 'text-yellow-500',
      bg: 'bg-yellow-500/10',
      href: '/calendar',
    },
    {
      name: 'Drafts',
      value: metrics?.draft_posts ?? 0,
      icon: PenLine,
      color: 'text-blue-500',
      bg: 'bg-blue-500/10',
      href: '/content',
    },
    {
      name: 'Failed',
      value: metrics?.failed_posts ?? 0,
      icon: AlertCircle,
      color: metrics?.failed_posts ? 'text-red-500' : 'text-muted-foreground',
      bg: metrics?.failed_posts ? 'bg-red-500/10' : 'bg-muted/40',
      href: '/content',
    },
  ]

  if (metricsLoading) {
    return (
      <div className="space-y-6">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1rem' }}>
          {[0, 1, 2, 3].map(i => (
            <Card key={i} className="h-24">
              <CardContent className="flex items-center justify-between p-6">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-12 w-12 rounded-full" />
              </CardContent>
            </Card>
          ))}
        </div>
        <Card>
          <CardHeader><CardTitle>Recent Activity</CardTitle></CardHeader>
          <CardContent>
            <div className="space-y-4">
              {[1, 2, 3].map(i => <Skeleton key={i} className="h-16 w-full" />)}
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Welcome header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{greeting(user?.name ?? null)}</h1>
          <p className="text-muted-foreground mt-1">
            Here&apos;s your social media overview for the last 30 days.
          </p>
        </div>
        <Button asChild data-tour="create-post">
          <Link href="/content/new">
            <Plus className="mr-2 h-4 w-4" />
            New Post
          </Link>
        </Button>
      </div>

      {/* Stats cards */}
      <div
        data-tour="stats"
        style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1rem' }}
      >
        {stats.map(stat => (
          <Link key={stat.name} href={stat.href} className="block">
            <Card className="hover:border-primary/40 transition-colors cursor-pointer h-full">
              <CardContent className="flex items-center justify-between p-6">
                <div>
                  <p className="text-sm font-medium text-muted-foreground">{stat.name}</p>
                  <p className="text-3xl font-bold mt-1">{stat.value}</p>
                </div>
                <div className={cn('p-3 rounded-full', stat.bg)}>
                  <stat.icon className={cn('h-6 w-6', stat.color)} />
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      {/* Week calendar */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="text-base">This Week</CardTitle>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/calendar">
              <Calendar className="mr-1.5 h-3.5 w-3.5" />
              Calendar
            </Link>
          </Button>
        </CardHeader>
        <CardContent>
          <WeekCalendar posts={scheduledPosts} />
        </CardContent>
      </Card>

      {/* Best posting window heatmap */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <div>
            <CardTitle className="text-base">Best Time to Post</CardTitle>
            <p className="text-xs text-muted-foreground mt-0.5">Engagement score by day and time</p>
          </div>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/analytics">
              <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
              Analytics
            </Link>
          </Button>
        </CardHeader>
        <CardContent>
          <PostingHeatmap topPosts={topPosts ?? []} />
        </CardContent>
      </Card>

      {/* Top posts + Quick actions */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '1.5rem' }}>
        {/* Top performing posts */}
        <Card data-tour="top-posts">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Top Performing Posts</CardTitle>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/analytics">
                View all <ExternalLink className="ml-1 h-3 w-3" />
              </Link>
            </Button>
          </CardHeader>
          <CardContent>
            {postsLoading ? (
              <div className="space-y-4">
                {[1, 2, 3].map(i => <Skeleton key={i} className="h-16 w-full" />)}
              </div>
            ) : !topPosts?.length ? (
              <EmptyState
                icon={Sparkles}
                title="No published posts yet"
                description="Create and publish your first post to see engagement metrics here."
                primaryAction={{ label: 'Create a post', href: '/content/new', icon: Plus }}
                className="py-8"
              />
            ) : (
              <div className="space-y-1">
                {topPosts.map((post: TopPost, idx: number) => {
                  const platformColor = PLATFORM_COLOR[post.platform?.toLowerCase() ?? ''] ?? '#6b7280'
                  return (
                    <Link
                      key={`${post.post_id}-${post.platform ?? ''}-${idx}`}
                      href={`/content/${post.post_id}/edit`}
                      className="flex items-center justify-between p-3 rounded-lg hover:bg-accent transition-colors"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div
                          className="h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 text-white text-xs font-bold"
                          style={{ backgroundColor: platformColor }}
                        >
                          {(post.platform?.[0] ?? '?').toUpperCase()}
                        </div>
                        <div className="min-w-0">
                          <p className="font-medium truncate text-sm">
                            {sanitizePostContent(post.content_text).slice(0, 70)}
                            {sanitizePostContent(post.content_text).length > 70 ? '…' : ''}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {post.platform}
                            {post.published_at ? ` • ${formatRelativeTime(post.published_at)}` : ''}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1 text-xs text-muted-foreground shrink-0 ml-3">
                        <TrendingUp className="h-3.5 w-3.5" />
                        {(post.engagement ?? 0).toLocaleString()}
                      </div>
                    </Link>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Quick actions */}
        <Card data-tour="quick-actions">
          <CardHeader>
            <CardTitle>Quick Actions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Button variant="outline" className="w-full justify-start gap-3" asChild>
              <Link href="/content/new">
                <PenLine className="h-4 w-4" />
                Create Post
              </Link>
            </Button>
            <Button variant="outline" className="w-full justify-start gap-3" asChild>
              <Link href="/calendar">
                <Calendar className="h-4 w-4" />
                Open Calendar
              </Link>
            </Button>
            <Button variant="outline" className="w-full justify-start gap-3" asChild>
              <Link href="/media">
                <Image className="h-4 w-4" />
                Upload Media
              </Link>
            </Button>
            <Button variant="outline" className="w-full justify-start gap-3" asChild>
              <Link href="/accounts">
                <Users className="h-4 w-4" />
                Connect Accounts
              </Link>
            </Button>
            <Button variant="outline" className="w-full justify-start gap-3" asChild>
              <Link href="/analytics">
                <BarChart2 className="h-4 w-4" />
                View Analytics
              </Link>
            </Button>
            <Button variant="outline" className="w-full justify-start gap-3" asChild>
              <Link href="/workflows">
                <Send className="h-4 w-4" />
                Workflows
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
