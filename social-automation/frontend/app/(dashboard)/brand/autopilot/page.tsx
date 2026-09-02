'use client'

import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { ArrowLeft, Bot, Play, TrendingUp, Clock, CheckCircle2, XCircle, Sparkles } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { brandApi } from '@/services/api'
import toast from 'react-hot-toast'
import { useBrand } from '@/hooks/useQueries'

export default function BrandAutopilotPage() {
  const { data: brand, isLoading } = useBrand()
  const [days, setDays] = useState(7)
  const [minScore, setMinScore] = useState(4)
  const [result, setResult] = useState<any>(null)

  const trendsQuery = useQuery({
    queryKey: ['brand-trends'],
    queryFn: () => brandApi.getTrends(),
    select: (r) => r.data,
    enabled: !!brand,
  })

  const autopilotMutation = useMutation({
    mutationFn: () => brandApi.runAutopilot({ days, min_compliance_score: minScore }),
    onSuccess: (r) => {
      setResult(r.data)
      if (r.data.created_count > 0) {
        toast.success(`${r.data.created_count} drafts created`)
      } else if (r.data.error) {
        toast.error(r.data.error)
      } else {
        toast(r.data.message || 'No drafts created')
      }
    },
    onError: () => toast.error('Autopilot failed'),
  })

  if (isLoading) {
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

  const trends = trendsQuery.data

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/brand"><Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button></Link>
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Bot className="h-6 w-6" /> Brand Autopilot</h1>
          <p className="text-sm text-muted-foreground">Autonomously generate on-brand content for empty calendar slots</p>
        </div>
      </div>

      {/* Autopilot Configuration */}
      <Card>
        <CardHeader>
          <CardTitle>Run Autopilot</CardTitle>
          <CardDescription>The agent will find empty calendar slots, select topics from your messaging pillars, generate content, and create drafts that pass compliance checks.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">Days ahead: {days}</label>
              <input type="range" min={1} max={14} value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-full accent-primary" />
            </div>
            <div>
              <label className="text-sm font-medium">Min compliance score: {minScore}/5</label>
              <input type="range" min={1} max={5} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} className="w-full accent-primary" />
            </div>
          </div>
          <Button onClick={() => autopilotMutation.mutate()} disabled={autopilotMutation.isPending} size="lg" className="w-full">
            {autopilotMutation.isPending ? (
              <><Sparkles className="mr-2 h-5 w-5 animate-spin" /> Generating...</>
            ) : (
              <><Play className="mr-2 h-5 w-5" /> Run Autopilot</>
            )}
          </Button>
        </CardContent>
      </Card>

      {/* Results */}
      {result && (
        <Card>
          <CardHeader>
            <CardTitle>Autopilot Results</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {result.error ? (
              <p className="text-red-600">{result.error}</p>
            ) : (
              <>
                <div className="grid grid-cols-3 gap-4 text-center">
                  <div>
                    <p className="text-3xl font-bold text-green-600">{result.created_count}</p>
                    <p className="text-sm text-muted-foreground">Drafts Created</p>
                  </div>
                  <div>
                    <p className="text-3xl font-bold text-yellow-600">{result.skipped_count}</p>
                    <p className="text-sm text-muted-foreground">Skipped (low compliance)</p>
                  </div>
                  <div>
                    <p className="text-3xl font-bold">{result.slots_filled}</p>
                    <p className="text-sm text-muted-foreground">Slots Filled</p>
                  </div>
                </div>
                {result.drafts?.length > 0 && (
                  <div className="space-y-2">
                    <p className="font-medium">Created Drafts:</p>
                    {result.drafts.map((d: any, i: number) => (
                      <div key={i} className="rounded-md border p-3 space-y-1">
                        <div className="flex items-center gap-2">
                          <Badge variant="secondary">{d.topic}</Badge>
                          <Badge variant="outline">Score: {d.compliance_score}/5</Badge>
                          <span className="text-xs text-muted-foreground flex items-center gap-1"><Clock className="h-3 w-3" />{new Date(d.scheduled_at).toLocaleString()}</span>
                        </div>
                        <p className="text-sm text-muted-foreground">{d.content_preview}...</p>
                      </div>
                    ))}
                  </div>
                )}
                {result.message && <p className="text-muted-foreground">{result.message}</p>}
              </>
            )}
          </CardContent>
        </Card>
      )}

      {/* Trend Scout */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><TrendingUp className="h-5 w-5" /> Trend Scout</CardTitle>
          <CardDescription>Trending topics from Twitter/X, Reddit, and your top-performing posts</CardDescription>
        </CardHeader>
        <CardContent>
          {trendsQuery.isLoading ? (
            <p className="text-muted-foreground">Loading trends...</p>
          ) : trends ? (
            <div className="space-y-4">
              {trends.twitter_trends?.length > 0 && (
                <div>
                  <p className="font-medium mb-2">Twitter/X Trends</p>
                  <div className="flex flex-wrap gap-2">
                    {trends.twitter_trends.map((t: any, i: number) => (
                      <Badge key={i} variant="secondary">{t.name} ({t.tweet_volume?.toLocaleString() || '—'})</Badge>
                    ))}
                  </div>
                </div>
              )}
              {trends.reddit_hot?.length > 0 && (
                <div>
                  <p className="font-medium mb-2">Reddit Hot Posts</p>
                  <div className="space-y-1">
                    {trends.reddit_hot.slice(0, 5).map((r: any, i: number) => (
                      <div key={i} className="text-sm">
                        <span className="text-muted-foreground">r/{r.subreddit}:</span> {r.title} <span className="text-xs text-muted-foreground">({r.score} upvotes)</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {trends.top_posts?.length > 0 && (
                <div>
                  <p className="font-medium mb-2">Your Top-Performing Posts</p>
                  <div className="space-y-1">
                    {trends.top_posts.map((p: any, i: number) => (
                      <div key={i} className="text-sm">
                        <span className="text-muted-foreground">{p.impressions} impressions:</span> {p.title}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {(!trends.twitter_trends?.length && !trends.reddit_hot?.length && !trends.top_posts?.length) && (
                <p className="text-muted-foreground text-sm">No trends available. Set TWITTER_BEARER_TOKEN for Twitter trends.</p>
              )}
            </div>
          ) : (
            <p className="text-muted-foreground text-sm">Failed to load trends.</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
