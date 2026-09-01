'use client'

import { useState, useEffect } from 'react'
import { ArrowLeft, Plus, X, Sparkles, Loader2 } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'
import { useBrand, useUpdateBrandVoice } from '@/hooks/useQueries'
import { brandApi } from '@/services/api'
import toast from 'react-hot-toast'

const TONE_DIMENSIONS = [
  { key: 'formality', label: 'Formality', leftLabel: 'Casual', rightLabel: 'Formal' },
  { key: 'playfulness', label: 'Playfulness', leftLabel: 'Serious', rightLabel: 'Playful' },
  { key: 'authority', label: 'Authority', leftLabel: 'Humble', rightLabel: 'Authoritative' },
  { key: 'friendliness', label: 'Friendliness', leftLabel: 'Distant', rightLabel: 'Friendly' },
  { key: 'technical', label: 'Technical Depth', leftLabel: 'Simple', rightLabel: 'Technical' },
]

interface MessagingPillar {
  pillar: string
  description: string
}

export default function BrandVoicePage() {
  const { data: brand, isLoading } = useBrand()
  const updateVoice = useUpdateBrandVoice()

  const [tones, setTones] = useState<Record<string, number>>({})
  const [messagingPillars, setMessagingPillars] = useState<MessagingPillar[]>([])
  const [newPillar, setNewPillar] = useState('')
  const [newPillarDesc, setNewPillarDesc] = useState('')
  const [bannedPhrases, setBannedPhrases] = useState<string[]>([])
  const [newBanned, setNewBanned] = useState('')
  const [preferredPhrases, setPreferredPhrases] = useState<string[]>([])
  const [newPreferred, setNewPreferred] = useState('')
  const [exampleContent, setExampleContent] = useState('')
  const [voiceSignature, setVoiceSignature] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeText, setAnalyzeText] = useState('')

  useEffect(() => {
    if (brand?.voice) {
      setTones(brand.voice.tone_dimensions || {})
      const pillars = (brand.voice.messaging_pillars || []).map((p: Record<string, string>) => ({
        pillar: p.pillar || p.title || '',
        description: p.description || '',
      }))
      setMessagingPillars(pillars)
      setBannedPhrases(brand.voice.banned_phrases || [])
      setPreferredPhrases(brand.voice.preferred_phrases || [])
      setExampleContent(brand.voice.example_content || '')
      const sig = brand.voice.voice_signature || {}
      setVoiceSignature(typeof sig === 'object' && !Array.isArray(sig) ? JSON.stringify(sig, null, 2) : '')
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

  // Ensure all tone dimensions have a default value of 3
  const getToneValue = (key: string) => tones[key] ?? 3

  const handleSave = () => {
    // Build tone_dimensions with defaults for all 5 dimensions
    const fullTones: Record<string, number> = {}
    for (const dim of TONE_DIMENSIONS) {
      fullTones[dim.key] = getToneValue(dim.key)
    }

    // Parse voice signature if provided
    let parsedSignature: Record<string, unknown> = {}
    if (voiceSignature.trim()) {
      try {
        parsedSignature = JSON.parse(voiceSignature)
      } catch {
        // If not valid JSON, store as a simple description string
        parsedSignature = { description: voiceSignature.trim() }
      }
    }

    updateVoice.mutate({
      tone_dimensions: fullTones,
      messaging_pillars: messagingPillars,
      banned_phrases: bannedPhrases,
      preferred_phrases: preferredPhrases,
      example_content: exampleContent || undefined,
      voice_signature: parsedSignature,
    })
  }

  const handleAnalyzeVoice = async () => {
    if (!analyzeText.trim()) {
      toast.error('Paste 1-3 content samples to analyze')
      return
    }
    setAnalyzing(true)
    try {
      const res = await brandApi.analyzeVoice({ samples: [analyzeText] })
      const result = res.data as {
        tone_dimensions?: Record<string, number>
        messaging_pillars?: Array<{ pillar: string; description: string }>
        banned_phrases?: string[]
        preferred_phrases?: string[]
        voice_signature?: Record<string, unknown>
      }
      if (result.tone_dimensions) setTones(result.tone_dimensions)
      if (result.messaging_pillars) setMessagingPillars(result.messaging_pillars)
      if (result.banned_phrases) setBannedPhrases(result.banned_phrases)
      if (result.preferred_phrases) setPreferredPhrases(result.preferred_phrases)
      if (result.voice_signature) setVoiceSignature(JSON.stringify(result.voice_signature, null, 2))
      toast.success('Voice analyzed from your content')
    } catch {
      toast.error('Failed to analyze voice')
    } finally {
      setAnalyzing(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/brand">
          <Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button>
        </Link>
        <h1 className="text-2xl font-bold">Voice & Tone</h1>
      </div>

      {/* AI Voice Analyzer */}
      <Card className="border-primary/30">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-primary" />
            AI Voice Analyzer
          </CardTitle>
          <CardDescription>Paste 1-3 examples of your content. AI will analyze tone, extract messaging pillars, and suggest banned/preferred phrases.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            value={analyzeText}
            onChange={(e) => setAnalyzeText(e.target.value)}
            rows={5}
            placeholder="Paste 1-3 paragraphs of content that represents your brand voice. Your website copy, a blog post, a social media post you're proud of..."
          />
          <Button
            type="button"
            variant="outline"
            onClick={handleAnalyzeVoice}
            disabled={analyzing || !analyzeText.trim()}
            className="w-full"
          >
            {analyzing ? (
              <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Analyzing...</>
            ) : (
              <><Sparkles className="mr-2 h-4 w-4" /> Analyze Voice from Content</>
            )}
          </Button>
        </CardContent>
      </Card>

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
                <span className="font-medium">{dim.label}: {getToneValue(dim.key)}</span>
                <span className="text-muted-foreground">{dim.rightLabel}</span>
              </div>
              <input
                type="range"
                min={1}
                max={5}
                step={1}
                value={getToneValue(dim.key)}
                onChange={(e) => setTones({ ...tones, [dim.key]: parseInt(e.target.value) })}
                className="w-full accent-primary"
              />
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Messaging Pillars */}
      <Card>
        <CardHeader>
          <CardTitle>Messaging Pillars</CardTitle>
          <CardDescription>Core themes your content should always reinforce</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-2">
            <Input
              value={newPillar}
              onChange={(e) => setNewPillar(e.target.value)}
              placeholder="Pillar name (e.g. Cost savings)"
            />
            <div className="flex gap-2">
              <Input
                value={newPillarDesc}
                onChange={(e) => setNewPillarDesc(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newPillar.trim()) {
                    e.preventDefault()
                    setMessagingPillars([...messagingPillars, { pillar: newPillar.trim(), description: newPillarDesc.trim() }])
                    setNewPillar('')
                    setNewPillarDesc('')
                  }
                }}
                placeholder="Short description (e.g. Save 30%+ vs enterprise providers)"
              />
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  if (newPillar.trim()) {
                    setMessagingPillars([...messagingPillars, { pillar: newPillar.trim(), description: newPillarDesc.trim() }])
                    setNewPillar('')
                    setNewPillarDesc('')
                  }
                }}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <div className="space-y-2">
            {messagingPillars.map((p, i) => (
              <div key={i} className="flex items-start justify-between rounded-lg border p-3">
                <div>
                  <div className="font-medium text-sm">{p.pillar}</div>
                  {p.description && <div className="text-sm text-muted-foreground mt-1">{p.description}</div>}
                </div>
                <button
                  onClick={() => setMessagingPillars(messagingPillars.filter((_, idx) => idx !== i))}
                  className="text-muted-foreground hover:text-foreground ml-2 mt-0.5"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
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

      <Card>
        <CardHeader>
          <CardTitle>Voice Signature</CardTitle>
          <CardDescription>A short description of your brand voice personality (used by AI for tone matching)</CardDescription>
        </CardHeader>
        <CardContent>
          <Textarea
            value={voiceSignature}
            onChange={(e) => setVoiceSignature(e.target.value)}
            rows={3}
            placeholder="e.g. Pragmatic, direct, anti-BS, confident. Speaks to startups and SMBs like a trusted technical advisor — not a corporate vendor."
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
