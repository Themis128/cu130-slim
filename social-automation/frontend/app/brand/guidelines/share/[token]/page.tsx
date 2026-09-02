'use client'

import { useParams } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { BookOpen } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import api from '@/services/api'

export default function SharedBrandGuidelinesPage() {
  const params = useParams<{ token: string }>()
  const { data: guidelines, isLoading } = useQuery({
    queryKey: ['shared-guidelines', params.token],
    queryFn: () => api.get(`/brand/guidelines/share/${params.token}`),
    select: (r) => r.data,
    retry: false,
  })

  if (isLoading) {
    return <div className="flex items-center justify-center min-h-screen"><p className="text-muted-foreground">Loading guidelines...</p></div>
  }

  if (!guidelines) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <BookOpen className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
          <p className="text-muted-foreground">Brand guidelines not found or link has expired.</p>
        </div>
      </div>
    )
  }

  const content = guidelines.content || {}

  return (
    <div className="min-h-screen bg-background">
      <div className="max-w-4xl mx-auto py-8 px-4 space-y-6">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-bold">Brand Guidelines</h1>
          {guidelines.version && <Badge variant="secondary">v{guidelines.version}</Badge>}
        </div>

        {content.brand && (
          <Card>
            <CardHeader><CardTitle>Brand Overview</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              <div><span className="font-medium">Name:</span> {content.brand.name}</div>
              {content.brand.industry && <div><span className="font-medium">Industry:</span> {content.brand.industry}</div>}
              {content.brand.tagline && <div><span className="font-medium">Tagline:</span> {content.brand.tagline}</div>}
              {content.brand.positioning && <div><span className="font-medium">Positioning:</span> {content.brand.positioning}</div>}
              {content.brand.mission && <div><span className="font-medium">Mission:</span> {content.brand.mission}</div>}
              {content.brand.values?.length > 0 && (
                <div>
                  <span className="font-medium">Values:</span>
                  <div className="flex flex-wrap gap-2 mt-1">
                    {content.brand.values.map((v: string, i: number) => <Badge key={i} variant="outline">{v}</Badge>)}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {content.voice && Object.keys(content.voice).length > 0 && (
          <Card>
            <CardHeader><CardTitle>Voice &amp; Tone</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              {content.voice.tone_dimensions && Object.keys(content.voice.tone_dimensions).length > 0 && (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                  {Object.entries(content.voice.tone_dimensions).map(([key, val]) => (
                    <div key={key} className="flex items-center justify-between rounded border px-3 py-1 text-sm">
                      <span className="capitalize">{key}</span>
                      <span className="font-medium">{val as number}/5</span>
                    </div>
                  ))}
                </div>
              )}
              {content.voice.banned_phrases?.length > 0 && (
                <div>
                  <span className="font-medium">Banned Phrases:</span>
                  <div className="flex flex-wrap gap-2 mt-1">
                    {content.voice.banned_phrases.map((p: string, i: number) => <Badge key={i} variant="destructive">{p}</Badge>)}
                  </div>
                </div>
              )}
              {content.voice.preferred_phrases?.length > 0 && (
                <div>
                  <span className="font-medium">Preferred Phrases:</span>
                  <div className="flex flex-wrap gap-2 mt-1">
                    {content.voice.preferred_phrases.map((p: string, i: number) => <Badge key={i} variant="secondary">{p}</Badge>)}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {content.visual && Object.keys(content.visual).length > 0 && (
          <Card>
            <CardHeader><CardTitle>Visual Identity</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-4">
                {content.visual.primary_color && (
                  <div className="text-center">
                    <div className="h-16 w-16 rounded-lg border" style={{ backgroundColor: content.visual.primary_color }} />
                    <p className="text-xs mt-1">Primary</p>
                  </div>
                )}
                {content.visual.accent_color && (
                  <div className="text-center">
                    <div className="h-16 w-16 rounded-lg border" style={{ backgroundColor: content.visual.accent_color }} />
                    <p className="text-xs mt-1">Accent</p>
                  </div>
                )}
                {content.visual.neutral_colors?.map((c: string, i: number) => (
                  <div key={i} className="text-center">
                    <div className="h-16 w-16 rounded-lg border" style={{ backgroundColor: c }} />
                    <p className="text-xs mt-1">Neutral</p>
                  </div>
                ))}
              </div>
              {content.visual.font_heading && <div><span className="font-medium">Heading Font:</span> {content.visual.font_heading}</div>}
              {content.visual.font_body && <div><span className="font-medium">Body Font:</span> {content.visual.font_body}</div>}
              {content.visual.image_style && <div><span className="font-medium">Image Style:</span> {content.visual.image_style}</div>}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
