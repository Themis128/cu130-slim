'use client'

import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Activity, TrendingUp, TrendingDown, Minus, Eye, Share2, Heart, Calendar } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { brandApi } from '@/services/api'
import { useBrand } from '@/hooks/useQueries'

export default function BrandHealthPage() {
  const { data: brand, isLoading: brandLoading } = useBrand()

  const { data: health, isLoading: healthLoading } = useQuery({
    queryKey: ['brand-health'],
    queryFn: () => brandApi.getHealth(),
    select: (r) => r.data,
    enabled: !!brand,
  })

  if (brandLoading || healthLoading) {
    return <div className="flex items-center justify-center min-h-[60vh]"><p className="text-muted-foreground">Loading...</p></div>
  }

  if (!brand) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground mb-4">Create a brand first</p>
        <Link href="/brand"><Button>Go to Brand</Button></Link>
      </div>
    )
  }

  if (!health) {
    return (
      <div className="max-w-4xl mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Link href="/brand"><Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button></Link>
          <h1 className="text-2xl font-bold">Brand Health</h1>
        </div>
        <Card>
          <CardContent className="py-12 text-center">
            <Activity className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
            <p className="text-muted-foreground">Health data not available yet. Collect mentions and take competitor snapshots first.</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  const scoreColor = (score: number) => {
    if (score >= 70) return 'text-green-600'
    if (score >= 50) return 'text-yellow-600'
    return 'text-red-600'
  }

  const scoreIcon = (score: number) => {
    if (score >= 70) return <TrendingUp className="h-5 w-5 text-green-600" />
    if (score >= 50) return <Minus className="h-5 w-5 text-yellow-600" />
    return <TrendingDown className="h-5 w-5 text-red-600" />
  }

  const metrics = [
    { label: 'Sentiment', score: health.sentiment, icon: <Heart className="h-5 w-5" /> },
    { label: 'Reach', score: health.reach, icon: <Eye className="h-5 w-5" /> },
    { label: 'Share of Voice', score: health.share_of_voice, icon: <Share2 className="h-5 w-5" /> },
    { label: 'Engagement', score: health.engagement, icon: <TrendingUp className="h-5 w-5" /> },
    { label: 'Consistency', score: health.consistency, icon: <Calendar className="h-5 w-5" /> },
  ]

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/brand"><Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button></Link>
        <h1 className="text-2xl font-bold">Brand Health</h1>
      </div>

      {/* Overall Score */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            Overall Health Score
          </CardTitle>
          <CardDescription>Combined score from sentiment, reach, share of voice, engagement, and posting consistency</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-4">
            <span className={`text-6xl font-bold ${scoreColor(health.overall)}`}>{health.overall}</span>
            <div className="space-y-1">
              <div className="flex items-center gap-2">{scoreIcon(health.overall)}<span className="text-lg font-medium">{health.overall >= 70 ? 'Healthy' : health.overall >= 50 ? 'Needs Attention' : 'At Risk'}</span></div>
              <p className="text-sm text-muted-foreground">Based on {health.mention_count} mentions and {health.total_engagement} total engagement</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Component Scores */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {metrics.map((m) => (
          <Card key={m.label}>
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2 text-muted-foreground">{m.icon}<span className="text-sm">{m.label}</span></div>
                {scoreIcon(m.score)}
              </div>
              <p className={`text-3xl font-bold ${scoreColor(m.score)}`}>{m.score}</p>
              <div className="mt-2 h-2 bg-muted rounded-full overflow-hidden">
                <div className={`h-full ${m.score >= 70 ? 'bg-green-600' : m.score >= 50 ? 'bg-yellow-600' : 'bg-red-600'}`} style={{ width: `${m.score}%` }} />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Summary Stats */}
      <Card>
        <CardHeader><CardTitle>Summary</CardTitle></CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <p className="text-2xl font-bold">{health.mention_count}</p>
              <p className="text-sm text-muted-foreground">Mentions (30d)</p>
            </div>
            <div>
              <p className="text-2xl font-bold">{health.total_engagement}</p>
              <p className="text-sm text-muted-foreground">Total Engagement</p>
            </div>
            <div>
              <p className="text-2xl font-bold">{health.avg_sentiment > 0 ? '+' : ''}{health.avg_sentiment}</p>
              <p className="text-sm text-muted-foreground">Avg Sentiment</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
