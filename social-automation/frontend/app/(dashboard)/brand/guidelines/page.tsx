'use client'

import { ArrowLeft, BookOpen, Share2, RefreshCw } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useBrand, useCompileGuidelines } from '@/hooks/useQueries'

export default function BrandGuidelinesPage() {
  const { data: brand, isLoading } = useBrand()
  const compileGuidelines = useCompileGuidelines()

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

  const guidelines = brand.guidelines
  const content = guidelines?.content || {}

  const handleCompile = () => compileGuidelines.mutate()
  const handleShare = () => {
    if (guidelines?.share_token) {
      const url = `${window.location.origin}/brand/guidelines?token=${guidelines.share_token}`
      navigator.clipboard.writeText(url)
    }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link href="/brand">
            <Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button>
          </Link>
          <h1 className="text-2xl font-bold">Brand Guidelines</h1>
        </div>
        <div className="flex gap-2">
          {guidelines?.share_token && (
            <Button variant="outline" onClick={handleShare}>
              <Share2 className="mr-2 h-4 w-4" /> Copy Share Link
            </Button>
          )}
          <Button onClick={handleCompile} disabled={compileGuidelines.isPending}>
            <RefreshCw className={`mr-2 h-4 w-4 ${compileGuidelines.isPending ? 'animate-spin' : ''}`} />
            {guidelines ? 'Recompile' : 'Compile'}
          </Button>
        </div>
      </div>

      {!guidelines ? (
        <Card>
          <CardContent className="py-12 text-center">
            <BookOpen className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
            <p className="text-muted-foreground mb-4">
              No guidelines compiled yet. Click &quot;Compile&quot; to generate a shareable brand guidelines document from your brand identity, voice, and visual settings.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {guidelines.version && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Badge variant="secondary">v{guidelines.version}</Badge>
              <span>Last updated: {new Date(guidelines.updated_at).toLocaleDateString()}</span>
            </div>
          )}

          {/* Brand section */}
          {content.brand && (
            <Card>
              <CardHeader>
                <CardTitle>Brand Overview</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div><span className="font-medium">Name:</span> {content.brand.name}</div>
                {content.brand.industry && <div><span className="font-medium">Industry:</span> {content.brand.industry}</div>}
                {content.brand.tagline && <div><span className="font-medium">Tagline:</span> {content.brand.tagline}</div>}
                {content.brand.positioning && <div><span className="font-medium">Positioning:</span> {content.brand.positioning}</div>}
                {content.brand.mission && <div><span className="font-medium">Mission:</span> {content.brand.mission}</div>}
                {content.brand.values?.length > 0 && (
                  <div>
                    <span className="font-medium">Values:</span>
                    <div className="flex flex-wrap gap-2 mt-1">
                      {content.brand.values.map((v: string, i: number) => (
                        <Badge key={i} variant="outline">{v}</Badge>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Voice section */}
          {content.voice && Object.keys(content.voice).length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Voice & Tone</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {content.voice.tone_dimensions && Object.keys(content.voice.tone_dimensions).length > 0 && (
                  <div>
                    <span className="font-medium">Tone Dimensions:</span>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-2 mt-2">
                      {Object.entries(content.voice.tone_dimensions).map(([key, val]) => (
                        <div key={key} className="flex items-center justify-between rounded border px-3 py-1 text-sm">
                          <span className="capitalize">{key}</span>
                          <span className="font-medium">{val as number}/5</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {content.voice.banned_phrases?.length > 0 && (
                  <div>
                    <span className="font-medium">Banned Phrases:</span>
                    <div className="flex flex-wrap gap-2 mt-1">
                      {content.voice.banned_phrases.map((p: string, i: number) => (
                        <Badge key={i} variant="destructive">{p}</Badge>
                      ))}
                    </div>
                  </div>
                )}
                {content.voice.preferred_phrases?.length > 0 && (
                  <div>
                    <span className="font-medium">Preferred Phrases:</span>
                    <div className="flex flex-wrap gap-2 mt-1">
                      {content.voice.preferred_phrases.map((p: string, i: number) => (
                        <Badge key={i} variant="secondary">{p}</Badge>
                      ))}
                    </div>
                  </div>
                )}
                {content.voice.example_content && (
                  <div>
                    <span className="font-medium">Example Content:</span>
                    <p className="mt-1 text-sm text-muted-foreground italic">{content.voice.example_content}</p>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Visual section */}
          {content.visual && Object.keys(content.visual).length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Visual Identity</CardTitle>
              </CardHeader>
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
                {content.visual.photography_direction && <div><span className="font-medium">Photography:</span> {content.visual.photography_direction}</div>}
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
