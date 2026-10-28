'use client'

import { useState } from 'react'
import { ArrowLeft, ArrowRight, Check, Loader2, Wand2 } from 'lucide-react'
import { Button } from './Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './Card'
import { Input } from './Input'
import { Label } from './Label'
import { Textarea } from './Textarea'
import { ColorPalettePicker } from './ColorPalettePicker'
import { ToneSliders } from './ToneSliders'
import { brandApi } from '@/services/api'
import toast from 'react-hot-toast'

interface BrandKitWizardProps {
  onComplete?: (brand: unknown) => void
}

const STEPS = ['Brand Basics', 'Voice & Tone', 'Visual Identity'] as const

export function BrandKitWizard({ onComplete }: BrandKitWizardProps) {
  const [step, setStep] = useState(0)
  const [extracting, setExtracting] = useState(false)
  const [saving, setSaving] = useState(false)

  // Step 1: Brand basics
  const [name, setName] = useState('')
  const [industry, setIndustry] = useState('')
  const [websiteUrl, setWebsiteUrl] = useState('')
  const [tagline, setTagline] = useState('')
  const [mission, setMission] = useState('')
  const [positioning, setPositioning] = useState('')
  const [values, setValues] = useState<string[]>([])
  const [newValue, setNewValue] = useState('')

  // Step 2: Voice
  const [tones, setTones] = useState<Record<string, number>>({})
  const [bannedPhrases, setBannedPhrases] = useState<string[]>([])
  const [preferredPhrases, setPreferredPhrases] = useState<string[]>([])
  const [analyzeText, setAnalyzeText] = useState('')

  // Step 3: Visual
  const [primaryColor, setPrimaryColor] = useState('#0b1220')
  const [accentColor, setAccentColor] = useState('#22d3e6')
  const [neutralColors, setNeutralColors] = useState<string[]>([])
  const [fontHeading, setFontHeading] = useState('')
  const [fontBody, setFontBody] = useState('')
  const [imageStyle, setImageStyle] = useState('')

  const handleExtract = async () => {
    if (!websiteUrl) return
    setExtracting(true)
    try {
      const res = await brandApi.extractFromUrl({ url: websiteUrl })
      const data = res.data
      if (data.name) setName(data.name)
      if (data.industry) setIndustry(data.industry)
      if (data.tagline) setTagline(data.tagline)
      if (data.mission) setMission(data.mission)
      if (data.positioning_statement) setPositioning(data.positioning_statement)
      if (data.values) setValues(data.values)
      if (data.visual) {
        if (data.visual.primary_color) setPrimaryColor(data.visual.primary_color)
        if (data.visual.accent_color) setAccentColor(data.visual.accent_color)
        if (data.visual.neutral_colors) setNeutralColors(data.visual.neutral_colors)
        if (data.visual.font_heading) setFontHeading(data.visual.font_heading)
        if (data.visual.font_body) setFontBody(data.visual.font_body)
        if (data.visual.image_style) setImageStyle(data.visual.image_style)
      }
      toast.success('Brand kit extracted from URL')
    } catch {
      toast.error('Extraction failed — fill in manually')
    } finally {
      setExtracting(false)
    }
  }

  const handleAnalyzeVoice = async () => {
    if (!analyzeText.trim()) return
    setExtracting(true)
    try {
      const res = await brandApi.analyzeVoice({ samples: [analyzeText] })
      const data = res.data
      if (data.tone_dimensions) setTones(data.tone_dimensions)
      if (data.banned_phrases) setBannedPhrases(data.banned_phrases)
      if (data.preferred_phrases) setPreferredPhrases(data.preferred_phrases)
      toast.success('Voice analyzed from sample')
    } catch {
      toast.error('Voice analysis failed — adjust sliders manually')
    } finally {
      setExtracting(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      // Step 1: Create brand
      const createRes = await brandApi.create({
        name,
        industry,
        website_url: websiteUrl,
        tagline,
        mission,
        positioning_statement: positioning,
        values,
      })
      const brand = createRes.data

      // Step 2: Update voice
      await brandApi.updateVoice({
        tone_dimensions: tones,
        banned_phrases: bannedPhrases,
        preferred_phrases: preferredPhrases,
        example_content: analyzeText,
      })

      // Step 3: Update visual
      await brandApi.updateVisual({
        primary_color: primaryColor,
        accent_color: accentColor,
        neutral_colors: neutralColors,
        font_heading: fontHeading,
        font_body: fontBody,
        image_style: imageStyle,
      })

      toast.success('Brand kit created successfully')
      onComplete?.(brand)
    } catch {
      toast.error('Failed to save brand kit')
    } finally {
      setSaving(false)
    }
  }

  const canProceed = () => {
    if (step === 0) return !!name
    if (step === 1) return true
    return true
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Step indicator */}
      <div className="flex items-center justify-between">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center">
            <div className={`flex items-center justify-center h-8 w-8 rounded-full text-sm font-medium ${
              i <= step ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'
            }`}>
              {i < step ? <Check className="h-4 w-4" /> : i + 1}
            </div>
            <span className={`ml-2 text-sm ${i <= step ? 'font-medium' : 'text-muted-foreground'}`}>{label}</span>
            {i < STEPS.length - 1 && <div className={`mx-3 h-px w-8 ${i < step ? 'bg-primary' : 'bg-muted'}`} />}
          </div>
        ))}
      </div>

      {/* Step 1: Brand Basics */}
      {step === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Brand Basics</CardTitle>
            <CardDescription>Start with the essentials — or extract from your website</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex gap-2">
              <Input
                placeholder="https://yourbrand.com"
                value={websiteUrl}
                onChange={(e) => setWebsiteUrl(e.target.value)}
              />
              <Button variant="outline" onClick={handleExtract} disabled={extracting || !websiteUrl}>
                {extracting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
                Extract
              </Button>
            </div>
            <div>
              <Label>Brand Name *</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Inc" />
            </div>
            <div>
              <Label>Industry</Label>
              <Input value={industry} onChange={(e) => setIndustry(e.target.value)} placeholder="SaaS, E-commerce,..." />
            </div>
            <div>
              <Label>Tagline</Label>
              <Input value={tagline} onChange={(e) => setTagline(e.target.value)} placeholder="Build better, ship faster" />
            </div>
            <div>
              <Label>Positioning Statement</Label>
              <Textarea value={positioning} onChange={(e) => setPositioning(e.target.value)} placeholder="For [audience] who [need], [brand] is [category] that [benefit]..." />
            </div>
            <div>
              <Label>Mission</Label>
              <Textarea value={mission} onChange={(e) => setMission(e.target.value)} placeholder="Our mission is..." />
            </div>
            <div>
              <Label>Values</Label>
              <div className="flex gap-2 mb-2">
                <Input value={newValue} onChange={(e) => setNewValue(e.target.value)} placeholder="Innovation" onKeyDown={(e) => { if (e.key === 'Enter' && newValue) { setValues([...values, newValue]); setNewValue('') }}} />
                <Button variant="outline" onClick={() => { if (newValue) { setValues([...values, newValue]); setNewValue('') } }}>Add</Button>
              </div>
              <div className="flex flex-wrap gap-2">
                {values.map((v, i) => (
                  <span key={i} className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-sm">
                    {v}
                    <button onClick={() => setValues(values.filter((_, idx) => idx !== i))} className="text-muted-foreground hover:text-foreground">×</button>
                  </span>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Step 2: Voice & Tone */}
      {step === 1 && (
        <Card>
          <CardHeader>
            <CardTitle>Voice & Tone</CardTitle>
            <CardDescription>Paste a sample of your content to analyze, or adjust sliders manually</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label>Content Sample for Analysis</Label>
              <Textarea
                value={analyzeText}
                onChange={(e) => setAnalyzeText(e.target.value)}
                placeholder="Paste a paragraph of your brand's content..."
                rows={4}
              />
              <Button variant="outline" className="mt-2" onClick={handleAnalyzeVoice} disabled={extracting || !analyzeText.trim()}>
                {extracting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
                Analyze Voice
              </Button>
            </div>
            <div>
              <Label>Tone Dimensions</Label>
              <ToneSliders dimensions={tones} onChange={setTones} />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Step 3: Visual Identity */}
      {step === 2 && (
        <Card>
          <CardHeader>
            <CardTitle>Visual Identity</CardTitle>
            <CardDescription>Define your brand colors and typography</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <ColorPalettePicker
              primary={primaryColor}
              accent={accentColor}
              neutrals={neutralColors}
              onChange={({ primary, accent, neutrals }) => {
                if (primary) setPrimaryColor(primary)
                if (accent) setAccentColor(accent)
                if (neutrals) setNeutralColors(neutrals)
              }}
            />
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>Heading Font</Label>
                <Input value={fontHeading} onChange={(e) => setFontHeading(e.target.value)} placeholder="Inter" />
              </div>
              <div>
                <Label>Body Font</Label>
                <Input value={fontBody} onChange={(e) => setFontBody(e.target.value)} placeholder="Work Sans" />
              </div>
            </div>
            <div>
              <Label>Image Style</Label>
              <Input value={imageStyle} onChange={(e) => setImageStyle(e.target.value)} placeholder="Minimal, bright, product-focused" />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Navigation */}
      <div className="flex justify-between">
        <Button variant="outline" onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0}>
          <ArrowLeft className="mr-2 h-4 w-4" /> Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button onClick={() => setStep(step + 1)} disabled={!canProceed()}>
            Next <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
        ) : (
          <Button onClick={handleSave} disabled={saving || !name}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            Create Brand Kit
          </Button>
        )}
      </div>
    </div>
  )
}
