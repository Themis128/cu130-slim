'use client'

import { useQuery } from '@tanstack/react-query'
import { TrendingUp, Users, FileText, Clock, ExternalLink, Plus } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Skeleton } from '@/components/ui/Skeleton'
import { useOverviewMetrics, useTopPosts } from '@/hooks/useQueries'
import { formatRelativeTime } from '@/lib/utils'
import Link from 'next/link'

const stats = [
  { name: 'Total Posts', value: 'total_posts', icon: FileText, color: 'text-blue-500', bg: 'bg-blue-500/10' },
  { name: 'Published', value: 'published_posts', icon: TrendingUp, color: 'text-green-500', bg: 'bg-green-500/10' },
  { name: 'Scheduled', value: 'scheduled_posts', icon: Clock, color: 'text-yellow-500', bg: 'bg-yellow-500/10' },
  { name: 'Connected Accounts', value: 'connected_accounts', icon: Users, color: 'text-purple-500', bg: 'bg-purple-500/10' },
]

export function DashboardPage() {
  const { data: metrics, isLoading: metricsLoading } = useOverviewMetrics(30)
  const { data: topPosts, isLoading: postsLoading } = useTopPosts(5)

  if (metricsLoading) {
    return (
      <div className="space-y-6">
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {stats.map(() => (
            <Card key={Math.random()} className="h-24">
              <CardContent className="flex items-center justify-between p-6">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-12 w-12 rounded-full" />
              </CardContent>
            </Card>
          ))}
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Recent Activity</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Welcome header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground mt-1">
            Overview of your social media automation
          </p>
        </div>
        <Button asChild>
          <Link href="/content/new">
            <Plus className="mr-2 h-4 w-4" />
            New Post
          </Link>
        </Button>
      </div>

      {/* Stats cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => {
          const value = metrics?.[stat.value as keyof typeof metrics] ?? 0
          return (
            <Card key={stat.name}>
              <CardContent className="flex items-center justify-between p-6">
                <div>
                  <p className="text-sm font-medium text-muted-foreground">{stat.name}</p>
                  <p className="text-3xl font-bold mt-1">{value}</p>
                </div>
                <div className={cn('p-3 rounded-full', stat.bg)}>
                  <stat.icon className={cn('h-6 w-6', stat.color)} />
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      {/* Top posts and quick actions */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Top performing posts */}
        <Card className="lg:col-span-2">
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
                {[1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : topPosts?.length === 0 ? (
              <div className="py-8 text-center text-muted-foreground">
                No posts yet. <Link href="/content/new" className="text-primary hover:underline">Create your first post</Link>
              </div>
            ) : (
              <div className="space-y-3">
                {topPosts?.map((post) => (
                  <div
                    key={post.id}
                    className="flex items-center justify-between p-3 rounded-lg hover:bg-accent transition-colors"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                        <FileText className="h-5 w-5 text-primary" />
                      </div>
                      <div className="min-w-0">
                        <p className="font-medium truncate">{post.content?.slice(0, 60)}...</p>
                        <p className="text-sm text-muted-foreground">
                          {post.platform} • {formatRelativeTime(post.published_at || post.created_at)}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3 text-sm text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <TrendingUp className="h-3.5 w-3.5" />
                        {post.engagement_count || 0}
                      </span>
                      <Badge variant={post.status === 'published' ? 'success' : 'secondary'}>
                        {post.status}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Quick actions */}
        <Card>
          <CardHeader>
            <CardTitle>Quick Actions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Button variant="outline" className="w-full justify-start gap-3" asChild>
              <Link href="/content/new">
                <FileText className="h-4 w-4" />
                Create Post
              </Link>
            </Button>
            <Button variant="outline" className="w-full justify-start gap-3" asChild>
              <Link href="/workflows">
                <TrendingUp className="h-4 w-4" />
                Browse Workflows
              </Link>
            </Button>
            <Button variant="outline" className="w-full justify-start gap-3" asChild>
              <Link href="/media">
                <FileText className="h-4 w-4" />
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
                <TrendingUp className="h-4 w-4" />
                View Analytics
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function cn(...classes: (string | undefined | null | false)[]) {
  return classes.filter(Boolean).join(' ')
}