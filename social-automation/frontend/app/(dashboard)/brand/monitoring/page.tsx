'use client'

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, RefreshCw, MessageCircle, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { brandApi } from '@/services/api'
import toast from 'react-hot-toast'
import { useBrand } from '@/hooks/useQueries'

export default function BrandMonitoringPage() {
  const { data: brand, isLoading } = useBrand()
  const queryClient = useQueryClient()

  const { data: mentions = [], isFetching } = useQuery({
    queryKey: ['brand-mentions'],
    queryFn: () => brandApi.listMentions({ limit: 50 }),
    select: (r) => r.data,
    enabled: !!brand,
  })

  const collectMutation = useMutation({
    mutationFn: () => brandApi.collectMentions(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['brand-mentions'] })
      toast.success('Mentions collected')
    },
    onError: () => toast.error('Failed to collect mentions'),
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

  const sentimentIcon = (s: string | null) => {
    if (s === 'positive') return <TrendingUp className="h-4 w-4 text-green-600" />
    if (s === 'negative') return <TrendingDown className="h-4 w-4 text-red-600" />
    return <Minus className="h-4 w-4 text-muted-foreground" />
  }

  const sentimentBadge = (s: string | null) => {
    if (s === 'positive') return <Badge variant="default" className="bg-green-600">Positive</Badge>
    if (s === 'negative') return <Badge variant="destructive">Negative</Badge>
    return <Badge variant="secondary">Neutral</Badge>
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link href="/brand"><Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button></Link>
          <h1 className="text-2xl font-bold">Brand Monitoring</h1>
        </div>
        <Button onClick={() => collectMutation.mutate()} disabled={collectMutation.isPending}>
          <RefreshCw className={`mr-2 h-4 w-4 ${collectMutation.isPending ? 'animate-spin' : ''}`} />
          Collect Mentions
        </Button>
      </div>

      {mentions.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <MessageCircle className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
            <p className="text-muted-foreground mb-2">No mentions collected yet</p>
            <p className="text-sm text-muted-foreground">Click &quot;Collect Mentions&quot; to search Twitter, Reddit, and Google News for your brand.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {mentions.map((m: any) => (
            <Card key={m.id}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 space-y-1">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">{m.platform}</Badge>
                      {sentimentBadge(m.sentiment)}
                      <span className="text-xs text-muted-foreground">{m.engagement} engagement</span>
                    </div>
                    <p className="text-sm">{m.content}</p>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      {m.author && <span>by {m.author}</span>}
                      {m.mentioned_at && <span>• {new Date(m.mentioned_at).toLocaleDateString()}</span>}
                      {m.url && <a href={m.url} target="_blank" rel="noopener noreferrer" className="text-blue-500 hover:underline">View</a>}
                    </div>
                  </div>
                  <div className="flex-shrink-0">{sentimentIcon(m.sentiment)}</div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
