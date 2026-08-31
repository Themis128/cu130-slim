'use client'

import { useState, useEffect } from 'react'
import { ArrowLeft, Plus, X } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'
import { useBrand, useUpdateBrandVoice } from '@/hooks/useQueries'

const TONE_DIMENSIONS = [
  { key: 'formal', label: 'Formal', leftLabel: 'Casual', rightLabel: 'Formal' },
  { key: 'playful', label: 'Playfulness', leftLabel: 'Serious', rightLabel: 'Playful' },
  { key: 'authoritative', label: 'Authority', leftLabel: 'Humble', rightLabel: 'Authoritative' },
  { key: 'friendly', label: 'Friendliness', leftLabel: 'Distant', rightLabel: 'Friendly' },
  { key: 'technical', label: 'Technical Depth', leftLabel: 'Simple', rightLabel: 'Technical' },
]

export default function BrandVoicePage() {
  const { data: brand, isLoading } = useBrand()
  const updateVoice = useUpdateBrandVoice()

  const [tones, setTones] = useState<Record<string, number>>({})
  const [bannedPhrases, setBannedPhrases] = useState<string[]>([])
  const [newBanned, setNewBanned] = useState('')
  const [preferredPhrases, setPreferredPhrases] = useState<string[]>([])
  const [newPreferred, setNewPreferred] = useState('')
  const [exampleContent, setExampleContent] = useState('')

  useEffect(() => {
    if (brand?.voice) {
      setTones(brand.voice.tone_dimensions || {})
      setBannedPhrases(brand.voice.banned_phrases || [])
      setPreferredPhrases(brand.voice.preferred_phrases || [])
      setExampleContent(brand.voice.example_content || '')
    }
  }, [brand])

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

  const handleSave = () => {
    updateVoice.mutate({
      tone_dimensions: tones,
      banned_phrases: bannedPhrases,
      preferred_phrases: preferredPhrases,
      example_content: exampleContent || undefined,
    })
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/brand">
          <Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button>
        </Link>
        <h1 className="text-2xl font-bold">Voice & Tone</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Tone Dimensions</CardTitle>
          <CardDescription>Slide to define your brand&apos;s voice personality (1-5)</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {TONE_DIMENSIONS.map((dim) => (
            <div key={dim.key} className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">{dim.leftLabel}</span>
                <span className="font-medium">{dim.label}: {tones[dim.key] || 3}</span>
                <span className="text-muted-foreground">{dim.rightLabel}</span>
              </div>
              <input
                type="range"
                min={1}
                max={5}
                step={1}
                value={tones[dim.key] || 3}
                onChange={(e) => setTones({ ...tones, [dim.key]: parseInt(e.target.value) })}
                className="w-full accent-primary"
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Banned Phrases</CardTitle>
          <CardDescription>Phrases the AI should never use in your content</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input
              value={newBanned}
              onChange={(e) => setNewBanned(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newBanned.trim()) {
                  e.preventDefault()
                  setBannedPhrases([...bannedPhrases, newBanned.trim()])
                  setNewBanned('')
                }
              }}
              placeholder="e.g. synergy, game-changer, disrupt"
            />
            <Button type="button" variant="outline" onClick={() => {
              if (newBanned.trim()) { setBannedPhrases([...bannedPhrases, newBanned.trim()]); setNewBanned('') }
            }}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {bannedPhrases.map((p, i) => (
              <div key={i} className="flex items-center gap-1 rounded-full bg-red-500/10 text-red-700 px-3 py-1 text-sm">
                {p}
                <button onClick={() => setBannedPhrases(bannedPhrases.filter((_, idx) => idx !== i))}>
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Preferred Phrases</CardTitle>
          <CardDescription>Phrases the AI should prefer in your content</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input
              value={newPreferred}
              onChange={(e) => setNewPreferred(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newPreferred.trim()) {
                  e.preventDefault()
                  setPreferredPhrases([...preferredPhrases, newPreferred.trim()])
                  setNewPreferred('')
                }
              }}
              placeholder="e.g. zero friction, clear skies"
            />
            <Button type="button" variant="outline" onClick={() => {
              if (newPreferred.trim()) { setPreferredPhrases([...preferredPhrases, newPreferred.trim()]); setNewPreferred('') }
            }}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {preferredPhrases.map((p, i) => (
              <div key={i} className="flex items-center gap-1 rounded-full bg-green-500/10 text-green-700 px-3 py-1 text-sm">
                {p}
                <button onClick={() => setPreferredPhrases(preferredPhrases.filter((_, idx) => idx !== i))}>
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Example Content</CardTitle>
          <CardDescription>Paste a sample that embodies your brand voice</CardDescription>
        </CardHeader>
        <CardContent>
          <Textarea
            value={exampleContent}
            onChange={(e) => setExampleContent(e.target.value)}
            rows={6}
            placeholder="Paste 1-2 paragraphs of content that perfectly represents your brand voice..."
          />
        </CardContent>
      </Card>

      <div className="flex justify-end gap-2">
        <Link href="/brand"><Button variant="outline">Cancel</Button></Link>
        <Button onClick={handleSave} disabled={updateVoice.isPending}>
          {updateVoice.isPending ? 'Saving...' : 'Save Voice'}
        </Button>
      </div>
    </div>
  )
}
