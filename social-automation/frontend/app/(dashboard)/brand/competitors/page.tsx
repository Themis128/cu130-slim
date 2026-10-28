'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Plus, Camera } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { brandApi } from '@/services/api'
import toast from 'react-hot-toast'
import { useBrand } from '@/hooks/useQueries'

export default function BrandCompetitorsPage() {
  const { data: brand, isLoading } = useBrand()
  const queryClient = useQueryClient()
  const [competitorName, setCompetitorName] = useState('')

  const { data: snapshots = [] } = useQuery({
    queryKey: ['brand-competitors'],
    queryFn: () => brandApi.listCompetitors(),
    select: (r) => r.data,
    enabled: !!brand,
  })

  const snapshotMutation = useMutation({
    mutationFn: (name: string) => brandApi.snapshotCompetitor({ competitor_name: name, platform: 'twitter' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['brand-competitors'] })
      toast.success('Competitor snapshot taken')
      setCompetitorName('')
    },
    onError: () => toast.error('Failed to snapshot competitor'),
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

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/brand"><Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button></Link>
        <h1 className="text-2xl font-bold">Competitor Tracking</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Take Competitor Snapshot</CardTitle>
          <CardDescription>Track competitor metrics across platforms</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            <div className="flex-1">
              <Label htmlFor="competitor">Competitor Name</Label>
              <Input id="competitor" value={competitorName} onChange={(e) => setCompetitorName(e.target.value)} placeholder="Competitor brand name" />
            </div>
            <div className="flex items-end">
              <Button onClick={() => competitorName && snapshotMutation.mutate(competitorName)} disabled={snapshotMutation.isPending || !competitorName}>
                <Camera className="mr-2 h-4 w-4" /> Snapshot
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {snapshots.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Plus className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
            <p className="text-muted-foreground">No competitor snapshots yet</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {snapshots.map((s: any) => (
            <Card key={s.id}>
              <CardContent className="p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{s.competitor_name}</span>
                  <Badge variant="outline">{s.platform}</Badge>
                </div>
                <div className="grid grid-cols-3 gap-2 text-sm">
                  <div>
                    <span className="text-muted-foreground">Followers</span>
                    <p className="font-medium">{s.follower_count?.toLocaleString() || '—'}</p>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Engagement</span>
                    <p className="font-medium">{s.engagement_rate ? `${s.engagement_rate}%` : '—'}</p>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Posts</span>
                    <p className="font-medium">{s.post_count || '—'}</p>
                  </div>
                </div>
                {s.top_post_content && (
                  <p className="text-sm text-muted-foreground italic">{s.top_post_content.slice(0, 100)}...</p>
                )}
                <p className="text-xs text-muted-foreground">Snapshot: {new Date(s.snapshot_at).toLocaleDateString()}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
