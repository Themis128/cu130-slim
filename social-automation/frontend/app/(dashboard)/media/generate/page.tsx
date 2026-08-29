'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  ArrowLeft, Sparkles, Wand2, Image as ImageIcon, Loader2,
  RefreshCw, Check, Zap, Layers, RectangleHorizontal,
  RectangleVertical, Square, LayoutGrid,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { Input } from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Label } from '@/components/ui/Label'
import { useGenerateImage } from '@/hooks/useQueries'
import { aiApi, mediaApi } from '@/services/api'
import type { AxiosResponse } from 'axios'
import toast from 'react-hot-toast'
import Link from 'next/link'

const CF_TXT2IMG_MODEL = '@cf/black-forest-labs/flux-1-schnell'

const STYLE_PRESETS = [
  { id: 'photorealistic', label: 'Photorealistic', suffix: 'photorealistic, 8k, sharp details, natural lighting, DSLR quality' },
  { id: 'professional', label: 'Professional', suffix: 'professional corporate photography, clean background, studio lighting, LinkedIn post visual' },
  { id: 'cinematic', label: 'Cinematic', suffix: 'cinematic, dramatic lighting, film grain, wide angle, moody atmosphere, high contrast' },
  { id: 'illustration', label: 'Illustration', suffix: 'digital illustration, flat design, bold colours, clean lines, modern graphic design' },
  { id: 'abstract', label: 'Abstract', suffix: 'abstract art, geometric shapes, vibrant colours, minimalist, modern art print' },
  { id: 'dark', label: 'Dark Cinematic', suffix: 'dark aesthetic, deep shadows, neon accents, cyberpunk mood, dark background, dramatic' },
  { id: 'gradient', label: 'Gradient', suffix: 'smooth gradient background, minimal, soft lighting, modern design aesthetic, clean' },
  { id: 'tech', label: 'Tech / Futuristic', suffix: 'futuristic technology, holographic interface, circuit patterns, glowing blue neon, dark background, sci-fi aesthetic' },
]

interface AspectRatioPreset {
  id: string
  label: string
  icon: typeof Square
  width: number
  height: number
  description: string
}

const ASPECT_RATIOS: AspectRatioPreset[] = [
  { id: '1:1', label: '1:1', icon: Square, width: 1024, height: 1024, description: 'Social post' },
  { id: '16:9', label: '16:9', icon: RectangleHorizontal, width: 1024, height: 576, description: 'Banner / cover' },
  { id: '4:3', label: '4:3', icon: RectangleHorizontal, width: 1024, height: 768, description: 'Presentation' },
  { id: '9:16', label: '9:16', icon: RectangleVertical, width: 576, height: 1024, description: 'Story / reel' },
  { id: '4:5', label: '4:5', icon: RectangleVertical, width: 820, height: 1024, description: 'Instagram post' },
  { id: '1.91:1', label: '1.91:1', icon: RectangleHorizontal, width: 1024, height: 536, description: 'Link preview' },
]

interface ConferenceTemplate {
  id: string
  label: string
  category: string
  prompt: string
  style: string
  aspectRatio: string
  description: string
}

const CONFERENCE_TEMPLATES: ConferenceTemplate[] = [
  // Presentation Slides (projector-optimized, high contrast, bold)
  {
    id: 'slide-title',
    label: 'Title Slide',
    category: 'Presentation',
    prompt: 'Bold high-contrast presentation title slide background, deep dark navy blue gradient, subtle geometric light rays converging to center, very clean with large negative space for big text, projector-optimized, ultra sharp, no clutter',
    style: 'dark',
    aspectRatio: '16:9',
    description: 'Opening slide background',
  },
  {
    id: 'slide-section',
    label: 'Section Divider',
    category: 'Presentation',
    prompt: 'Clean section divider slide background, bold diagonal stripe of bright accent color cutting across dark background, high contrast, minimal, strong visual impact for projector display, plenty of space for section title text',
    style: 'dark',
    aspectRatio: '16:9',
    description: 'Section break slide',
  },
  {
    id: 'slide-content',
    label: 'Content Slide',
    category: 'Presentation',
    prompt: 'Minimal dark presentation slide background with very subtle geometric pattern on left edge only, deep charcoal to black gradient, maximum negative space on right for content, high contrast for projector, clean and professional',
    style: 'dark',
    aspectRatio: '16:9',
    description: 'Body content slide BG',
  },
  {
    id: 'slide-tech',
    label: 'Tech Slide',
    category: 'Presentation',
    prompt: 'Dark technical presentation background with faint glowing circuit board traces and subtle grid pattern, deep black with blue-green accent glow on edges, projector-optimized high contrast, space for code or diagrams',
    style: 'tech',
    aspectRatio: '16:9',
    description: 'Technical content slide',
  },
  {
    id: 'slide-quote',
    label: 'Quote Slide',
    category: 'Presentation',
    prompt: 'Elegant presentation quote slide background, single dramatic spotlight effect from above on dark background, subtle warm gradient, large open center space for quote text, projector-friendly high contrast, refined',
    style: 'cinematic',
    aspectRatio: '16:9',
    description: 'Quote / highlight slide',
  },
  {
    id: 'slide-closing',
    label: 'Closing Slide',
    category: 'Presentation',
    prompt: 'Professional closing slide background, dark gradient with subtle radial light burst from center, warm golden accent tones, thank you and QA visual, projector-optimized bold contrast, space for contact info',
    style: 'dark',
    aspectRatio: '16:9',
    description: 'Closing / Q&A slide',
  },
  {
    id: 'slide-43',
    label: '4:3 Slide BG',
    category: 'Presentation',
    prompt: 'Clean 4:3 presentation slide background for older projectors, deep dark gradient from midnight blue to black, subtle abstract wave pattern on bottom edge, maximum empty space for content, high contrast, sharp',
    style: 'dark',
    aspectRatio: '4:3',
    description: 'Legacy projector format',
  },
  {
    id: 'slide-data',
    label: 'Data / Chart Slide',
    category: 'Presentation',
    prompt: 'Minimal dark presentation background optimized for data visualization, very subtle grid lines in dark grey, deep black background, faint blue accent glow at bottom, maximum contrast for charts and graphs projected on screen',
    style: 'dark',
    aspectRatio: '16:9',
    description: 'Charts / graphs slide',
  },
  // Announcements & Promotion
  {
    id: 'event-announcement',
    label: 'Event Announcement',
    category: 'Promotion',
    prompt: 'A bold, eye-catching conference event graphic with a dynamic stage setup, bright spotlights illuminating an empty podium, large venue with rows of seats, professional atmosphere',
    style: 'cinematic',
    aspectRatio: '1:1',
    description: 'Main event announcement graphic',
  },
  {
    id: 'countdown',
    label: 'Countdown Post',
    category: 'Promotion',
    prompt: 'Abstract dynamic countdown clock visual with glowing numbers, motion blur trails of light, dark background with colorful accent streaks, excitement and urgency feeling',
    style: 'dark',
    aspectRatio: '1:1',
    description: '"X days until" countdown visual',
  },
  {
    id: 'early-bird',
    label: 'Early Bird / CTA',
    category: 'Promotion',
    prompt: 'Clean modern graphic with golden ticket or VIP pass floating with sparkle effects, luxury feel, dark gradient background with subtle gold accents, registration call to action visual',
    style: 'gradient',
    aspectRatio: '1:1',
    description: 'Registration promo graphic',
  },
  // Speakers & Sessions
  {
    id: 'speaker-card',
    label: 'Speaker Card',
    category: 'Speakers',
    prompt: 'Professional stage podium with dramatic single spotlight, microphone on stand, blurred audience in background, bokeh lights, clean composition with space for text overlay on the side',
    style: 'cinematic',
    aspectRatio: '1:1',
    description: 'Background for speaker info overlay',
  },
  {
    id: 'panel-discussion',
    label: 'Panel Discussion',
    category: 'Speakers',
    prompt: 'Modern conference panel setup with four comfortable chairs on stage, small round table with microphones, professional stage lighting, subtle branded backdrop, audience silhouettes',
    style: 'professional',
    aspectRatio: '16:9',
    description: 'Panel / fireside chat visual',
  },
  {
    id: 'keynote',
    label: 'Keynote Session',
    category: 'Speakers',
    prompt: 'Grand keynote stage with massive LED screen backdrop, dramatic blue and purple lighting, single podium center stage, packed auditorium, inspiring atmosphere',
    style: 'cinematic',
    aspectRatio: '16:9',
    description: 'Keynote announcement banner',
  },
  // Venue & Logistics
  {
    id: 'venue-exterior',
    label: 'Venue Shot',
    category: 'Venue',
    prompt: 'Modern glass convention center exterior at golden hour, warm light reflecting off windows, wide establishing shot, trees and landscaping, clear sky, inviting entrance',
    style: 'photorealistic',
    aspectRatio: '16:9',
    description: 'Venue showcase banner',
  },
  {
    id: 'networking',
    label: 'Networking Area',
    category: 'Venue',
    prompt: 'Vibrant conference networking lounge with standing tables, people mingling in groups, modern furniture, colorful lighting, coffee bar visible, warm atmosphere, busy energy',
    style: 'photorealistic',
    aspectRatio: '16:9',
    description: 'Networking / after-party visual',
  },
  {
    id: 'workshop',
    label: 'Workshop Room',
    category: 'Venue',
    prompt: 'Hands-on workshop classroom setup with laptops on tables, whiteboard with diagrams, small groups collaborating, bright well-lit room, learning environment, engaged participants',
    style: 'professional',
    aspectRatio: '16:9',
    description: 'Workshop or breakout session',
  },
  // Stories & Social
  {
    id: 'story-teaser',
    label: 'Story Teaser',
    category: 'Social',
    prompt: 'Vertical mobile-optimized teaser with blurred conference crowd background, dramatic lens flare, depth of field, energetic vibe with space for text in center',
    style: 'cinematic',
    aspectRatio: '9:16',
    description: 'Instagram / TikTok story',
  },
  {
    id: 'quote-card',
    label: 'Quote Card',
    category: 'Social',
    prompt: 'Elegant minimal background with subtle geometric pattern, soft gradient from deep navy to midnight blue, clean space for text overlay, professional and refined',
    style: 'gradient',
    aspectRatio: '1:1',
    description: 'Speaker quote background',
  },
  {
    id: 'social-banner',
    label: 'Social Banner',
    category: 'Social',
    prompt: 'Wide panoramic conference hall view with modern architecture, rows of filled seats, large screens showing presentation, professional lighting, busy atmosphere',
    style: 'photorealistic',
    aspectRatio: '1.91:1',
    description: 'LinkedIn / Twitter header',
  },
  // Branding & Misc
  {
    id: 'agenda-bg',
    label: 'Agenda Background',
    category: 'Branding',
    prompt: 'Clean abstract geometric background with subtle lines and shapes, minimal, muted professional colors, soft gradient, plenty of negative space for schedule text overlay',
    style: 'gradient',
    aspectRatio: '4:3',
    description: 'Schedule / agenda background',
  },
  {
    id: 'sponsor-bg',
    label: 'Sponsor Section',
    category: 'Branding',
    prompt: 'Elegant minimal dark background with subtle spotlight effect from above, professional corporate feeling, clean surface for logo placement, refined gradient',
    style: 'dark',
    aspectRatio: '16:9',
    description: 'Sponsor tier background',
  },
  {
    id: 'thank-you',
    label: 'Thank You',
    category: 'Branding',
    prompt: 'Warm celebratory confetti falling with golden sparkles, soft bokeh background, joyful atmosphere, event conclusion feeling, with space for thank you message',
    style: 'cinematic',
    aspectRatio: '1:1',
    description: 'Post-event thank you graphic',
  },
]

const TEMPLATE_CATEGORIES = [...new Set(CONFERENCE_TEMPLATES.map(t => t.category))]

function mediaDisplayUrl(storagePath?: string | null) {
  if (!storagePath) return ''
  const base = process.env.NEXT_PUBLIC_API_URL || '/api/v1'
  return `${base}/media/view?path=${encodeURIComponent(storagePath)}`
}

interface GeneratedImage {
  image_base64?: string
  storage_path?: string
  asset_id?: string
  prompt: string
  style: string
  width: number
  height: number
}

export default function GenerateImagePage() {
  const router = useRouter()
  const generateMutation = useGenerateImage()

  const [rawIdea, setRawIdea] = useState('')
  const [enhancedPrompt, setEnhancedPrompt] = useState('')
  const [negativePrompt, setNegativePrompt] = useState('')
  const [selectedStyle, setSelectedStyle] = useState('photorealistic')
  const [selectedAspect, setSelectedAspect] = useState('1:1')
  const [steps, setSteps] = useState(4)
  const [batchCount, setBatchCount] = useState(1)
  const [enhancing, setEnhancing] = useState(false)
  const [autoConfiguring, setAutoConfiguring] = useState(false)
  const [history, setHistory] = useState<GeneratedImage[]>([])
  const [generating, setGenerating] = useState(false)
  const [generatingIndex, setGeneratingIndex] = useState(0)
  const [templateFilter, setTemplateFilter] = useState<string | null>(null)

  const activePrompt = enhancedPrompt || rawIdea
  const aspect = ASPECT_RATIOS.find(a => a.id === selectedAspect) || ASPECT_RATIOS[0]

  const applyTemplate = (template: ConferenceTemplate) => {
    setRawIdea(template.prompt)
    setEnhancedPrompt('')
    setSelectedStyle(template.style)
    setSelectedAspect(template.aspectRatio)
    toast.success(`Template applied: ${template.label}`)
  }

  const handleAutoConfigure = async () => {
    if (!rawIdea.trim()) { toast.error('Enter an idea first'); return }
    setAutoConfiguring(true)
    try {
      const res = await aiApi.autoConfigurePrompt(rawIdea.trim(), 'image') as AxiosResponse<{
        style?: string; steps?: number; enhanced_prompt?: string; negative_prompt?: string
      }>
      const data = res?.data
      if (data) {
        if (data.style && STYLE_PRESETS.find(s => s.id === data.style)) setSelectedStyle(data.style)
        if (data.steps) setSteps(data.steps as 4 | 6 | 8)
        if (data.enhanced_prompt) setEnhancedPrompt(data.enhanced_prompt)
        if (data.negative_prompt) setNegativePrompt(data.negative_prompt)
        toast.success('Settings auto-configured by AI')
      }
    } catch {
      toast.error('Auto-configure failed')
    } finally {
      setAutoConfiguring(false)
    }
  }

  const handleEnhance = async () => {
    if (!rawIdea.trim()) { toast.error('Enter an idea first'); return }
    setEnhancing(true)
    try {
      const res = await aiApi.enhanceImagePrompt(rawIdea.trim(), selectedStyle)
      const data = (res as { data?: { prompt?: string; negative_prompt?: string } })?.data
      if (data?.prompt) {
        setEnhancedPrompt(data.prompt)
        setNegativePrompt(data.negative_prompt || '')
        toast.success('Prompt enhanced by AI')
      }
    } catch {
      toast.error('Prompt enhancement failed')
    } finally {
      setEnhancing(false)
    }
  }

  const handleGenerate = async () => {
    if (!activePrompt.trim()) { toast.error('Enter a prompt or idea first'); return }
    const stylePreset = STYLE_PRESETS.find(s => s.id === selectedStyle)
    const finalPrompt = enhancedPrompt
      ? enhancedPrompt
      : `${rawIdea.trim()}. ${stylePreset?.suffix ?? ''}`

    setGenerating(true)
    const results: GeneratedImage[] = []

    for (let i = 0; i < batchCount; i++) {
      setGeneratingIndex(i)
      try {
        const res = await generateMutation.mutateAsync({
          prompt: finalPrompt.trim(),
          options: {
            provider: 'cloudflare',
            model: CF_TXT2IMG_MODEL,
            steps,
            negative_prompt: negativePrompt,
            width: aspect.width,
            height: aspect.height,
          },
        })
        const data = (res as { data?: { image_base64?: string; storage_path?: string; asset_id?: string } })?.data
        if (data) {
          results.push({
            image_base64: data.image_base64,
            storage_path: data.storage_path,
            asset_id: data.asset_id,
            prompt: finalPrompt,
            style: selectedStyle,
            width: aspect.width,
            height: aspect.height,
          })
        }
      } catch {
        toast.error(`Generation ${i + 1} failed`)
      }
    }

    if (results.length > 0) {
      setHistory(prev => [...results.reverse(), ...prev].slice(0, 20))
    }
    setGenerating(false)
    setGeneratingIndex(0)
  }

  const latest = history[0]
  const filteredTemplates = templateFilter
    ? CONFERENCE_TEMPLATES.filter(t => t.category === templateFilter)
    : CONFERENCE_TEMPLATES

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="icon" asChild>
          <Link href="/media"><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <div>
          <h1 className="text-3xl font-bold tracking-tight">AI Image Generator</h1>
          <p className="text-muted-foreground mt-1">
            Generate conference visuals, social media graphics, banners, and more
          </p>
        </div>
      </div>

      {/* Conference Templates */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Layers className="h-4 w-4 text-primary" />
            Conference Templates
            <Badge variant="outline" className="text-[10px]">click to apply</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2 mb-3 flex-wrap">
            <button
              onClick={() => setTemplateFilter(null)}
              className={`text-xs font-medium px-3 py-1.5 rounded-full border transition-colors ${
                !templateFilter ? 'border-primary bg-primary text-primary-foreground' : 'border-border hover:bg-accent'
              }`}
            >
              All
            </button>
            {TEMPLATE_CATEGORIES.map(cat => (
              <button
                key={cat}
                onClick={() => setTemplateFilter(templateFilter === cat ? null : cat)}
                className={`text-xs font-medium px-3 py-1.5 rounded-full border transition-colors ${
                  templateFilter === cat ? 'border-primary bg-primary text-primary-foreground' : 'border-border hover:bg-accent'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-2">
            {filteredTemplates.map(t => {
              const aspectInfo = ASPECT_RATIOS.find(a => a.id === t.aspectRatio)
              return (
                <button
                  key={t.id}
                  onClick={() => applyTemplate(t)}
                  className="text-left p-2.5 rounded-lg border border-border hover:border-primary hover:bg-primary/5 transition-colors group"
                >
                  <p className="text-xs font-medium truncate group-hover:text-primary">{t.label}</p>
                  <p className="text-[10px] text-muted-foreground mt-0.5">{t.description}</p>
                  <div className="flex items-center gap-1 mt-1.5">
                    <Badge variant="outline" className="text-[9px] px-1 py-0">{t.aspectRatio}</Badge>
                    <Badge variant="outline" className="text-[9px] px-1 py-0 capitalize">{t.style}</Badge>
                  </div>
                </button>
              )
            })}
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Controls */}
        <div className="space-y-5">
          {/* Idea / prompt */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Wand2 className="h-4 w-4 text-primary" />
                Your Idea
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                value={rawIdea}
                onChange={e => { setRawIdea(e.target.value); setEnhancedPrompt('') }}
                placeholder="Describe what you want — e.g. 'keynote stage with dramatic lighting for a tech conference'"
                rows={3}
                className="text-sm"
              />
              <div className="flex gap-2">
                <Button
                  variant="default"
                  size="sm"
                  onClick={handleAutoConfigure}
                  disabled={autoConfiguring || enhancing || !rawIdea.trim()}
                  className="flex-1"
                  title="AI reads your prompt and auto-selects style, steps, and optimised prompt"
                >
                  {autoConfiguring
                    ? <><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Configuring…</>
                    : <><Zap className="mr-2 h-3.5 w-3.5" />Auto-configure</>}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleEnhance}
                  disabled={enhancing || autoConfiguring || !rawIdea.trim()}
                  className="flex-1"
                >
                  {enhancing
                    ? <><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Enhancing…</>
                    : <><Sparkles className="mr-2 h-3.5 w-3.5" />Enhance Prompt</>}
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Enhanced prompt */}
          {enhancedPrompt && (
            <Card className="border-primary/30 bg-primary/5">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-2">
                  <Sparkles className="h-3.5 w-3.5 text-primary" />
                  Enhanced Prompt
                  <Badge variant="outline" className="text-[10px]">AI-generated · editable</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <Textarea
                  value={enhancedPrompt}
                  onChange={e => setEnhancedPrompt(e.target.value)}
                  rows={4}
                  className="text-xs font-mono"
                />
                {negativePrompt && (
                  <div>
                    <Label className="text-xs text-muted-foreground">Negative prompt</Label>
                    <Input
                      value={negativePrompt}
                      onChange={e => setNegativePrompt(e.target.value)}
                      className="text-xs mt-1"
                    />
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Aspect Ratio */}
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Aspect Ratio</CardTitle></CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
                {ASPECT_RATIOS.map(a => {
                  const Icon = a.icon
                  return (
                    <button
                      key={a.id}
                      onClick={() => setSelectedAspect(a.id)}
                      className={`flex flex-col items-center gap-1 text-xs font-medium px-2 py-2.5 rounded border transition-colors ${
                        selectedAspect === a.id
                          ? 'border-primary bg-primary text-primary-foreground'
                          : 'border-border hover:bg-accent'
                      }`}
                    >
                      <Icon className="h-4 w-4" />
                      <span>{a.label}</span>
                      <span className={`text-[9px] ${selectedAspect === a.id ? 'text-primary-foreground/80' : 'text-muted-foreground'}`}>
                        {a.description}
                      </span>
                    </button>
                  )
                })}
              </div>
              <p className="text-[10px] text-muted-foreground mt-2 text-right">
                {aspect.width}×{aspect.height}px
              </p>
            </CardContent>
          </Card>

          {/* Style presets */}
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Style</CardTitle></CardHeader>
            <CardContent>
              <div className="grid grid-cols-4 gap-2">
                {STYLE_PRESETS.map(s => (
                  <button
                    key={s.id}
                    onClick={() => { setSelectedStyle(s.id); setEnhancedPrompt('') }}
                    className={[
                      'text-xs font-medium px-2 py-2 rounded border transition-colors',
                      selectedStyle === s.id
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border hover:bg-accent',
                    ].join(' ')}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Quality + Batch */}
          <div className="grid grid-cols-2 gap-4">
            <Card>
              <CardHeader className="pb-3"><CardTitle className="text-base">Quality</CardTitle></CardHeader>
              <CardContent>
                <div className="flex gap-2">
                  {[
                    { v: 4, label: 'Fast' },
                    { v: 6, label: 'Balanced' },
                    { v: 8, label: 'Best' },
                  ].map(opt => (
                    <button
                      key={opt.v}
                      onClick={() => setSteps(opt.v)}
                      className={[
                        'flex-1 text-xs font-medium py-2 rounded border transition-colors',
                        steps === opt.v
                          ? 'border-primary bg-primary text-primary-foreground'
                          : 'border-border hover:bg-accent',
                      ].join(' ')}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
                <p className="text-[10px] text-muted-foreground mt-2">
                  {steps} steps · more = sharper
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <LayoutGrid className="h-4 w-4" />
                  Batch
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex gap-2">
                  {[1, 2, 4].map(n => (
                    <button
                      key={n}
                      onClick={() => setBatchCount(n)}
                      className={[
                        'flex-1 text-xs font-medium py-2 rounded border transition-colors',
                        batchCount === n
                          ? 'border-primary bg-primary text-primary-foreground'
                          : 'border-border hover:bg-accent',
                      ].join(' ')}
                    >
                      {n}×
                    </button>
                  ))}
                </div>
                <p className="text-[10px] text-muted-foreground mt-2">
                  {batchCount === 1 ? 'Single image' : `${batchCount} variations`}
                </p>
              </CardContent>
            </Card>
          </div>

          <Button
            onClick={handleGenerate}
            disabled={generating || !activePrompt.trim()}
            className="w-full"
            size="lg"
          >
            {generating
              ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Generating {batchCount > 1 ? `${generatingIndex + 1}/${batchCount}` : ''}…</>
              : <><ImageIcon className="mr-2 h-4 w-4" />Generate {batchCount > 1 ? `${batchCount} Images` : 'Image'}</>}
          </Button>
        </div>

        {/* Right: Preview + History */}
        <div className="space-y-4">
          {/* Latest result */}
          <Card className="overflow-hidden">
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center justify-between">
                Preview
                {latest && (
                  <Badge variant="outline" className="text-[10px]">
                    {latest.width}×{latest.height}
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {generating ? (
                <div
                  className="bg-muted flex flex-col items-center justify-center gap-3 text-muted-foreground"
                  style={{ aspectRatio: `${aspect.width}/${aspect.height}`, maxHeight: '500px' }}
                >
                  <Loader2 className="h-8 w-8 animate-spin text-primary" />
                  <p className="text-sm">Generating with FLUX… {batchCount > 1 ? `(${generatingIndex + 1}/${batchCount})` : ''}</p>
                </div>
              ) : latest ? (
                <div className="relative">
                  {latest.storage_path ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                      src={mediaDisplayUrl(latest.storage_path)}
                      alt="Generated"
                      className="w-full object-cover"
                      style={{ aspectRatio: `${latest.width}/${latest.height}`, maxHeight: '500px' }}
                    />
                  ) : latest.image_base64 ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                      src={`data:image/png;base64,${latest.image_base64}`}
                      alt="Generated"
                      className="w-full object-cover"
                      style={{ aspectRatio: `${latest.width}/${latest.height}`, maxHeight: '500px' }}
                    />
                  ) : null}
                  <div className="absolute bottom-0 inset-x-0 bg-black/60 px-3 py-2 flex items-center justify-between">
                    <p className="text-[10px] text-white truncate flex-1 mr-2">{latest.prompt.slice(0, 120)}</p>
                    <div className="flex gap-1 shrink-0">
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-7 w-7 text-white hover:text-white hover:bg-white/20"
                        onClick={handleGenerate}
                        disabled={generating}
                        title="Regenerate"
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                      </Button>
                      {latest.storage_path && (
                        <Button
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7 text-white hover:text-white hover:bg-white/20"
                          onClick={() => router.push('/media')}
                          title="View in Media Library"
                        >
                          <Check className="h-3.5 w-3.5" />
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <div
                  className="bg-muted/40 flex flex-col items-center justify-center gap-3 text-muted-foreground"
                  style={{ aspectRatio: `${aspect.width}/${aspect.height}`, maxHeight: '400px' }}
                >
                  <ImageIcon className="h-12 w-12 opacity-20" />
                  <p className="text-sm">Your image will appear here</p>
                  <p className="text-xs text-muted-foreground">{aspect.width}×{aspect.height} · {aspect.description}</p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* History grid */}
          {history.length > 1 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Recent Generations ({history.length})</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-4 gap-2">
                  {history.slice(1).map((img, i) => (
                    <div
                      key={i}
                      className="rounded overflow-hidden border cursor-pointer hover:opacity-80 transition-opacity relative group"
                      style={{ aspectRatio: `${img.width}/${img.height}` }}
                      onClick={() => setHistory(prev => {
                        const copy = [...prev]
                        const item = copy.splice(i + 1, 1)[0]
                        return [item, ...copy]
                      })}
                    >
                      {img.storage_path ? (
                        /* eslint-disable-next-line @next/next/no-img-element */
                        <img src={mediaDisplayUrl(img.storage_path)} alt="" className="h-full w-full object-cover" />
                      ) : img.image_base64 ? (
                        /* eslint-disable-next-line @next/next/no-img-element */
                        <img src={`data:image/png;base64,${img.image_base64}`} alt="" className="h-full w-full object-cover" />
                      ) : null}
                      <div className="absolute inset-x-0 bottom-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity px-1 py-0.5">
                        <p className="text-[8px] text-white">{img.width}×{img.height} · {img.style}</p>
                      </div>
                    </div>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground mt-2">
                  All images saved to your <Link href="/media" className="underline">Media Library</Link>.
                </p>
              </CardContent>
            </Card>
          )}

          {latest?.storage_path && (
            <Button variant="outline" className="w-full" asChild>
              <Link href="/media">
                View All in Media Library
              </Link>
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
