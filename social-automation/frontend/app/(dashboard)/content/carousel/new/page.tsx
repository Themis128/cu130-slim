'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { toPng } from 'html-to-image'
import { Sparkles, ChevronLeft, ChevronRight, Download, Send, Loader2, Plus, Trash2, Palette, ArrowLeft, Check, Zap } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { useAccounts } from '@/hooks/useQueries'
import { useCreatePost, useUploadMedia } from '@/hooks/useQueries'
import { contentApi, aiApi } from '@/services/api'
import toast from 'react-hot-toast'

// ─── Types ────────────────────────────────────────────────────────────────────

interface Slide {
  title: string
  body: string
  highlight: string | null
  slide_type: 'cover' | 'content' | 'stat' | 'cta'
}

interface BrandConfig {
  logoText: string
  domain: string
  tagline: string
  showBranding: boolean
}

// ─── Themes ───────────────────────────────────────────────────────────────────

const THEMES = {
  cloudless: {
    name: 'Cloudless',
    bg: '#0b1220',
    accent: '#22d3e6',
    accent2: '#fb923c',
    text: '#dde4f0',
    subtext: '#8895ac',
    highlight: '#22d3e6',
    card: '#142033',
    border: '#2a3550',
  },
  navy: {
    name: 'Navy Pro',
    bg: '#0A1628',
    accent: '#E8B84B',
    accent2: '#E8B84B',
    text: '#FFFFFF',
    subtext: '#94A3B8',
    highlight: '#E8B84B',
    card: '#132040',
    border: '#1e3a5f',
  },
  purple: {
    name: 'Indigo',
    bg: '#1E1B4B',
    accent: '#818CF8',
    accent2: '#F472B6',
    text: '#FFFFFF',
    subtext: '#A5B4FC',
    highlight: '#C7D2FE',
    card: '#2D2A6A',
    border: '#3730A3',
  },
  clean: {
    name: 'Clean White',
    bg: '#FFFFFF',
    accent: '#0b1220',
    accent2: '#22d3e6',
    text: '#0b1220',
    subtext: '#64748B',
    highlight: '#0b1220',
    card: '#F1F5F9',
    border: '#E2E8F0',
  },
}

type ThemeKey = keyof typeof THEMES

// ─── Cloudless Logo SVG ───────────────────────────────────────────────────────

function BrandLogo({ color, brand, size = 28 }: { color: string; brand: BrandConfig; size?: number }) {
  const [logo, domain] = [brand.logoText, brand.domain]
  const approxWidth = Math.max(120, (logo.length + domain.length) * 10 + 40)
  return (
    <svg width={approxWidth} height={size} viewBox={`0 0 ${approxWidth} ${size}`} fill="none" xmlns="http://www.w3.org/2000/svg">
      {/* Cloud + bolt icon */}
      <path d="M22 17H8C5.79 17 4 15.21 4 13C4 10.79 5.79 9 8 9C8.27 9 8.54 9.02 8.8 9.07C9.52 6.86 11.6 5.25 14 5.25C16.9 5.25 19.25 7.6 19.25 10.5V10.58C19.5 10.53 19.75 10.5 20 10.5C21.66 10.5 23 11.84 23 13.5C23 15.71 21.21 17 19 17"
        stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13 8.5L11 13H14L12 17.5" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      {/* Brand name */}
      <text x="30" y={size * 0.72} fontFamily="'Inter','Helvetica Neue',Arial,sans-serif" fontWeight="700" fontSize={size * 0.58} fill={color}>{logo}</text>
      <text x={30 + logo.length * size * 0.36} y={size * 0.72} fontFamily="'Inter','Helvetica Neue',Arial,sans-serif" fontWeight="400" fontSize={size * 0.58} fill={color} opacity="0.55">{domain ? `.${domain}` : ''}</text>
    </svg>
  )
}

// ─── Slide Renderer ───────────────────────────────────────────────────────────

const DEFAULT_BRAND: BrandConfig = {
  logoText: 'cloudless',
  domain: 'gr',
  tagline: 'Clear skies. Zero friction.',
  showBranding: true,
}

function SlideRenderer({ slide, theme, index, total, brand = DEFAULT_BRAND, backgroundImageUrl }: { slide: Slide; theme: ThemeKey; index: number; total: number; brand?: BrandConfig; backgroundImageUrl?: string }) {
  const t = THEMES[theme]
  const isCloudless = theme === 'cloudless'
  const branding = brand.showBranding

  return (
    <div
      style={{
        width: '1080px',
        height: '1080px',
        background: backgroundImageUrl ? `url(${backgroundImageUrl}) center/cover no-repeat` : t.bg,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: '72px 80px 56px',
        position: 'relative',
        fontFamily: "'Inter', 'Helvetica Neue', Arial, sans-serif",
        boxSizing: 'border-box',
        overflow: 'hidden',
      }}
    >
      {/* Dark overlay when using AI background image */}
      {backgroundImageUrl && (
        <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.52)', pointerEvents: 'none' }} />
      )}
      {/* Background decoration (hidden when AI background active) */}
      <div style={{
        position: 'absolute',
        top: '-200px',
        right: '-200px',
        width: '600px',
        height: '600px',
        borderRadius: '50%',
        background: t.accent,
        opacity: isCloudless ? 0.08 : 0.05,
        pointerEvents: 'none',
      }} />
      <div style={{
        position: 'absolute',
        bottom: '-100px',
        left: '-100px',
        width: '400px',
        height: '400px',
        borderRadius: '50%',
        background: isCloudless ? t.accent2 : t.accent,
        opacity: 0.05,
        pointerEvents: 'none',
      }} />
      {/* Cloudless: subtle grid texture */}
      {isCloudless && (
        <div style={{
          position: 'absolute', inset: 0,
          backgroundImage: `linear-gradient(${t.border}55 1px, transparent 1px), linear-gradient(90deg, ${t.border}55 1px, transparent 1px)`,
          backgroundSize: '80px 80px',
          opacity: 0.4,
          pointerEvents: 'none',
        }} />
      )}

      {/* Top bar — dual accent for Cloudless, single for others */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: '5px', background: isCloudless ? `linear-gradient(90deg, ${t.accent} 0%, ${t.accent2} 100%)` : t.accent }} />

      {/* Brand header row */}
      {branding && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          zIndex: 2,
          marginBottom: '32px',
          flexShrink: 0,
        }}>
          <BrandLogo color={t.accent} brand={brand} size={28} />
          <div style={{
            fontSize: '22px',
            color: t.subtext,
            fontWeight: 500,
            letterSpacing: '0.02em',
          }}>
            {String(index + 1).padStart(2, '0')} / {String(total).padStart(2, '0')}
          </div>
        </div>
      )}

      {/* Content */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: '40px', zIndex: 1 }}>
        {slide.slide_type === 'cover' && (
          <>
            {/* Tagline pill */}
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              background: `${t.accent}18`,
              border: `1px solid ${t.accent}44`,
              color: t.accent,
              padding: '8px 20px',
              borderRadius: '100px',
              fontSize: '24px',
              fontWeight: 600,
              letterSpacing: '0.04em',
              alignSelf: 'flex-start',
            }}>
              <span style={{ fontSize: '20px' }}>⚡</span> {brand.tagline}
            </div>
            <h1 style={{
              fontSize: '84px',
              fontWeight: 900,
              color: t.text,
              lineHeight: 1.05,
              margin: 0,
              letterSpacing: '-0.02em',
            }}>
              {slide.title}
            </h1>
            <p style={{
              fontSize: '34px',
              color: t.subtext,
              margin: 0,
              lineHeight: 1.5,
              maxWidth: '800px',
            }}>
              {slide.body}
            </p>
          </>
        )}

        {slide.slide_type === 'content' && (
          <>
            <h2 style={{
              fontSize: '68px',
              fontWeight: 800,
              color: t.text,
              lineHeight: 1.1,
              margin: 0,
              letterSpacing: '-0.02em',
            }}>
              {slide.title}
            </h2>
            {slide.highlight && (
              <div style={{
                background: t.card,
                borderLeft: `6px solid ${t.accent}`,
                padding: '24px 32px',
                borderRadius: '0 12px 12px 0',
                fontSize: '32px',
                fontWeight: 700,
                color: t.highlight,
              }}>
                {slide.highlight}
              </div>
            )}
            <p style={{
              fontSize: '32px',
              color: t.subtext,
              margin: 0,
              lineHeight: 1.6,
              maxWidth: '900px',
            }}>
              {slide.body}
            </p>
          </>
        )}

        {slide.slide_type === 'stat' && (
          <>
            <div style={{
              fontSize: '20px',
              color: t.subtext,
              fontWeight: 600,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}>
              Key Insight
            </div>
            <div style={{
              fontSize: '150px',
              fontWeight: 900,
              color: isCloudless ? t.accent2 : t.accent,
              lineHeight: 1,
              letterSpacing: '-0.04em',
            }}>
              {slide.highlight || slide.title}
            </div>
            <h2 style={{
              fontSize: '52px',
              fontWeight: 800,
              color: t.text,
              margin: 0,
              lineHeight: 1.2,
            }}>
              {slide.highlight ? slide.title : ''}
            </h2>
            <p style={{
              fontSize: '32px',
              color: t.subtext,
              margin: 0,
              lineHeight: 1.6,
              maxWidth: '900px',
            }}>
              {slide.body}
            </p>
          </>
        )}

        {slide.slide_type === 'cta' && (
          <>
            <h2 style={{
              fontSize: '80px',
              fontWeight: 900,
              color: t.text,
              lineHeight: 1.05,
              margin: 0,
              letterSpacing: '-0.02em',
            }}>
              {slide.title}
            </h2>
            <p style={{
              fontSize: '36px',
              color: t.subtext,
              margin: 0,
              lineHeight: 1.6,
              maxWidth: '900px',
            }}>
              {slide.body}
            </p>
            {slide.highlight && (
              <div style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '12px',
                background: t.accent,
                color: t.bg,
                padding: '20px 40px',
                borderRadius: '8px',
                fontSize: '32px',
                fontWeight: 800,
                alignSelf: 'flex-start',
              }}>
                {slide.highlight} →
              </div>
            )}
          </>
        )}
      </div>

      {/* Bottom: pagination dots + brand URL */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', zIndex: 1, flexShrink: 0, marginTop: '32px' }}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {Array.from({ length: total }).map((_, i) => (
            <div key={i} style={{
              width: i === index ? '28px' : '8px',
              height: '8px',
              borderRadius: '4px',
              background: i === index ? t.accent : t.subtext,
              opacity: i === index ? 1 : 0.35,
            }} />
          ))}
        </div>
        {branding && (
          <div style={{
            fontSize: '22px',
            color: t.subtext,
            fontWeight: 500,
            letterSpacing: '0.02em',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}>
            <span style={{ color: t.accent, fontWeight: 700 }}>{brand.logoText}</span>
            {brand.domain ? `.${brand.domain}` : ''}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

type Step = 'setup' | 'preview' | 'export'

export default function CarouselNewPage() {
  const router = useRouter()
  const { data: accounts } = useAccounts()
  const createPostMutation = useCreatePost()
  const uploadMediaMutation = useUploadMedia()
  // Step
  const [step, setStep] = useState<Step>('setup')

  // Setup form
  const [topic, setTopic] = useState('')
  const [numSlides, setNumSlides] = useState(6)
  const [platform, setPlatform] = useState('linkedin')
  const [tone, setTone] = useState('professional')
  const [includeCta, setIncludeCta] = useState(true)

  // Generated data
  const [slides, setSlides] = useState<Slide[]>([])
  const [caption, setCaption] = useState('')
  const [hashtags, setHashtags] = useState<string[]>([])
  const [generating, setGenerating] = useState(false)

  // Editor
  const [activeSlide, setActiveSlide] = useState(0)
  const [theme, setTheme] = useState<ThemeKey>('cloudless')
  const [brand, setBrand] = useState<BrandConfig>(DEFAULT_BRAND)
  const [showBrandPanel, setShowBrandPanel] = useState(false)
  // AI background images (index → media display URL)
  const [bgImages, setBgImages] = useState<Record<number, string>>({})
  const [generatingBgs, setGeneratingBgs] = useState(false)
  const [bgProgress, setBgProgress] = useState(0)
  const [autoConfiguring, setAutoConfiguring] = useState(false)

  // Export
  const [exporting, setExporting] = useState(false)
  const [exportedMediaIds, setExportedMediaIds] = useState<string[]>([])
  const [publishing, setPublishing] = useState(false)
  const [selectedPublishPlatforms, setSelectedPublishPlatforms] = useState<string[]>([])

  const slideRefs = useRef<(HTMLDivElement | null)[]>([])

  const connectedAccounts: Array<{ id: string; platform: string; account_type?: string; display_name: string | null; username: string | null }> = accounts || []

  // Auto-select all platforms when accounts load
  useEffect(() => {
    if (connectedAccounts.length === 0 || selectedPublishPlatforms.length > 0) return
    const allPlatforms = [...new Set(connectedAccounts.map(a => a.platform))]
    setSelectedPublishPlatforms(allPlatforms)
  }, [connectedAccounts]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Auto-configure from topic ────────────────────────────────────────────────

  const handleAutoConfigure = async () => {
    if (!topic.trim()) { toast.error('Enter a topic first'); return }
    setAutoConfiguring(true)
    try {
      const res = await aiApi.autoConfigurePrompt(topic.trim(), 'carousel')
      const data = (res as { data?: { platform?: string; tone?: string; num_slides?: number } })?.data
      if (data) {
        const PLATFORM_MAP: Record<string, string> = { linkedin: 'linkedin', instagram: 'instagram', twitter: 'linkedin' }
        const TONE_MAP: Record<string, string> = { professional: 'professional', casual: 'conversational', inspirational: 'inspiring', educational: 'educational', humorous: 'conversational' }
        if (data.platform && PLATFORM_MAP[data.platform]) setPlatform(PLATFORM_MAP[data.platform])
        if (data.tone && TONE_MAP[data.tone]) setTone(TONE_MAP[data.tone])
        if (data.num_slides && data.num_slides >= 4 && data.num_slides <= 10) setNumSlides(data.num_slides)
        toast.success('Settings auto-configured — review and generate')
      }
    } catch {
      toast.error('Auto-configure failed')
    } finally {
      setAutoConfiguring(false)
    }
  }

  // ── Step 1: Generate ────────────────────────────────────────────────────────

  const handleGenerate = async () => {
    if (!topic.trim()) { toast.error('Enter a topic first'); return }
    setGenerating(true)
    try {
      const res = await aiApi.generateCarousel({
        topic,
        num_slides: numSlides,
        platform,
        tone,
        include_cta: includeCta,
        provider: 'cloudflare',
      })
      setSlides(res.data.slides)
      setCaption(res.data.suggested_caption)
      setHashtags(res.data.hashtags)
      setActiveSlide(0)
      setStep('preview')
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail || 'Generation failed — check that Ollama is running or switch to a cloud provider')
    } finally {
      setGenerating(false)
    }
  }

  // ── AI Background Generation ─────────────────────────────────────────────────

  const handleGenerateBackgrounds = async () => {
    if (slides.length === 0) return
    setGeneratingBgs(true)
    setBgProgress(0)
    const newBgs: Record<number, string> = {}
    const apiBase = process.env.NEXT_PUBLIC_API_URL || '/api/v1'

    for (let i = 0; i < slides.length; i++) {
      const slide = slides[i]
      const visual = (
        `LinkedIn carousel background for a slide titled "${slide.title}". ` +
        `${slide.body}. Dark navy abstract tech aesthetic, cyan and soft orange accents, ` +
        `clean modern composition, square 1:1, no readable text, no logos, no watermark.`
      )
      try {
        const res = await fetch(`${apiBase}/ai/generate-image`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${localStorage.getItem('access_token') || ''}`,
          },
          body: JSON.stringify({
            prompt: visual,
            provider: 'cloudflare',
            model: '@cf/black-forest-labs/flux-1-schnell',
            steps: 4,
          }),
        })
        if (res.ok) {
          const data = await res.json()
          if (data.storage_path) {
            newBgs[i] = `${apiBase}/media/view?path=${encodeURIComponent(data.storage_path)}`
          } else if (data.image_base64) {
            newBgs[i] = `data:image/png;base64,${data.image_base64}`
          }
        }
      } catch { /* continue to next slide */ }
      setBgProgress(i + 1)
    }
    setBgImages(prev => ({ ...prev, ...newBgs }))
    setGeneratingBgs(false)
    toast.success(`${Object.keys(newBgs).length}/${slides.length} backgrounds generated`)
  }

  // ── Step 2: Edit ────────────────────────────────────────────────────────────

  const updateSlide = (i: number, field: keyof Slide, value: string) => {
    setSlides(prev => prev.map((s, idx) => idx === i ? { ...s, [field]: value } : s))
  }

  const addSlide = () => {
    setSlides(prev => [...prev, { title: 'New Slide', body: 'Add your content here.', highlight: null, slide_type: 'content' }])
    setActiveSlide(slides.length)
  }

  const removeSlide = (i: number) => {
    if (slides.length <= 2) { toast.error('Need at least 2 slides'); return }
    setSlides(prev => prev.filter((_, idx) => idx !== i))
    setActiveSlide(Math.min(activeSlide, slides.length - 2))
  }

  // ── Step 3: Export & Post ───────────────────────────────────────────────────

  const handleExport = useCallback(async () => {
    setExporting(true)
    const mediaIds: string[] = []
    try {
      for (let i = 0; i < slides.length; i++) {
        const node = slideRefs.current[i]
        if (!node) continue
        const dataUrl = await toPng(node, { width: 1080, height: 1080, pixelRatio: 1 })
        const blob = await (await fetch(dataUrl)).blob()
        const file = new File([blob], `slide-${i + 1}.png`, { type: 'image/png' })
        const result = await uploadMediaMutation.mutateAsync({ file, alt_text: `Slide ${i + 1}`, tags: 'carousel' })
        const id = (result as unknown as { data?: { id?: string }; id?: string })?.data?.id
          ?? (result as unknown as { id?: string })?.id
        if (id) mediaIds.push(id)
        toast.success(`Slide ${i + 1}/${slides.length} exported`)
      }
      setExportedMediaIds(mediaIds)
      // Pre-select all connected platforms
      setSelectedPublishPlatforms([...new Set(connectedAccounts.map(a => a.platform))])
      toast.success('All slides ready to publish!')
    } catch (err) {
      console.error(err)
      toast.error('Export failed — check console')
    } finally {
      setExporting(false)
    }
  }, [slides, uploadMediaMutation])

  const handlePublish = async (asDraft: boolean) => {
    if (exportedMediaIds.length === 0) { toast.error('Export slides first'); return }
    if (selectedPublishPlatforms.length === 0) { toast.error('Select at least one platform'); return }
    setPublishing(true)
    try {
      const fullCaption = `${caption}\n\n${hashtags.map(h => `#${h}`).join(' ')}`
      // Build targets for all selected platforms; for LinkedIn always use cloudless-gr org
      const seen = new Set<string>()
      const targets: Array<{ social_account_id: string }> = []
      for (const plat of selectedPublishPlatforms) {
        if (seen.has(plat)) continue
        let acct: typeof connectedAccounts[0] | undefined
        if (plat === 'linkedin') {
          acct = connectedAccounts.find(a => a.platform === 'linkedin' && a.account_type === 'organization')
            ?? connectedAccounts.find(a => a.platform === 'linkedin')
        } else {
          acct = connectedAccounts.find(a => a.platform === plat)
        }
        if (acct) { targets.push({ social_account_id: acct.id }); seen.add(plat) }
      }
      const createdPost = await createPostMutation.mutateAsync({
        content_text: fullCaption,
        hashtags,
        media_ids: exportedMediaIds,
        targets,
        metadata: {
          carousel: {
            slides,
            theme,
            brand,
            topic,
            platform,
            tone,
            num_slides: slides.length,
            provider: 'cloudflare',
          },
        },
      })
      if (!asDraft) {
        const postId = (createdPost as unknown as { data?: { id?: string } })?.data?.id
        if (postId) {
          await contentApi.publishNow(postId)
        }
        toast.success(`Carousel published to ${selectedPublishPlatforms.join(', ')}!`)
      } else {
        toast.success('Carousel saved as draft!')
      }
      router.push('/content')
    } catch {
      toast.error('Failed to publish')
    } finally {
      setPublishing(false)
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  const SLIDE_SCALE = 0.32

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="icon" onClick={() => router.push('/content')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Carousel Creator</h1>
          <p className="text-muted-foreground mt-1">AI-generated infographic slides ready to post</p>
        </div>
      </div>

      {/* Steps */}
      <div className="flex items-center gap-2">
        {(['setup', 'preview', 'export'] as Step[]).map((s, i) => (
          <div key={s} className="flex items-center gap-2">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold transition-colors ${
              step === s ? 'bg-primary text-primary-foreground'
              : (['setup', 'preview', 'export'].indexOf(step) > i) ? 'bg-green-500 text-white'
              : 'bg-muted text-muted-foreground'
            }`}>
              {(['setup', 'preview', 'export'].indexOf(step) > i) ? <Check className="h-4 w-4" /> : i + 1}
            </div>
            <span className="text-sm font-medium capitalize">{s === 'setup' ? 'Topic & Settings' : s === 'preview' ? 'Design & Edit' : 'Export & Post'}</span>
            {i < 2 && <ChevronRight className="h-4 w-4 text-muted-foreground" />}
          </div>
        ))}
      </div>

      {/* ── Step 1: Setup ── */}
      {step === 'setup' && (
        <Card>
          <CardHeader><CardTitle>What is your carousel about?</CardTitle></CardHeader>
          <CardContent className="space-y-6">
            <div className="space-y-2">
              <label className="text-sm font-medium">Topic / Main idea</label>
              <Textarea
                value={topic}
                onChange={e => setTopic(e.target.value)}
                placeholder="e.g. 5 reasons why remote teams outperform office teams in 2025"
                rows={3}
                className="text-base"
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={handleAutoConfigure}
              disabled={autoConfiguring || !topic.trim()}
              className="w-full"
            >
              {autoConfiguring
                ? <><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Auto-configuring…</>
                : <><Zap className="mr-2 h-3.5 w-3.5" />Auto-configure settings from topic</>}
            </Button>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Platform</label>
                <Select value={platform} onValueChange={setPlatform}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="linkedin">LinkedIn</SelectItem>
                    <SelectItem value="instagram">Instagram</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Number of slides</label>
                <Select value={String(numSlides)} onValueChange={v => setNumSlides(Number(v))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {[4, 5, 6, 7, 8, 9, 10].map(n => (
                      <SelectItem key={n} value={String(n)}>{n} slides</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Tone</label>
                <Select value={tone} onValueChange={setTone}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="professional">Professional</SelectItem>
                    <SelectItem value="conversational">Conversational</SelectItem>
                    <SelectItem value="educational">Educational</SelectItem>
                    <SelectItem value="inspiring">Inspiring</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Include CTA slide</label>
                <Select value={includeCta ? 'yes' : 'no'} onValueChange={v => setIncludeCta(v === 'yes')}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="yes">Yes</SelectItem>
                    <SelectItem value="no">No</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="col-span-2 flex items-center gap-2 rounded-lg border bg-muted/40 px-4 py-3">
                <Sparkles className="h-4 w-4 text-primary flex-shrink-0" />
                <div>
                  <p className="text-sm font-medium">Powered by Cloudflare Workers AI</p>
                  <p className="text-xs text-muted-foreground">Text: llama-3.3-70b · Images: FLUX Schnell · Fallback: HF → Ollama</p>
                </div>
              </div>
            </div>
            <Button onClick={handleGenerate} disabled={generating || !topic.trim()} className="w-full" size="lg">
              {generating ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Generating with AI…</> : <><Sparkles className="mr-2 h-4 w-4" />Generate Carousel</>}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* ── Step 2: Preview & Edit ── */}
      {step === 'preview' && slides.length > 0 && (
        <div className="grid grid-cols-[1fr_360px] gap-6">
          {/* Left: Slide preview */}
          <div className="space-y-4">
            {/* Theme + Brand picker */}
            <div className="flex items-center gap-3 flex-wrap">
              <Palette className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-medium">Theme:</span>
              {(Object.keys(THEMES) as ThemeKey[]).map(tk => (
                <button
                  key={tk}
                  onClick={() => setTheme(tk)}
                  className={`px-3 py-1 rounded text-xs font-medium border transition-colors ${
                    theme === tk ? 'border-primary bg-primary text-primary-foreground' : 'border-border hover:bg-accent'
                  }`}
                >
                  {tk === 'cloudless' ? '⚡ ' : ''}{THEMES[tk].name}
                </button>
              ))}
              <button
                onClick={() => setShowBrandPanel(v => !v)}
                className={`px-3 py-1 rounded text-xs font-medium border transition-colors ml-2 ${showBrandPanel ? 'border-primary bg-primary text-primary-foreground' : 'border-border hover:bg-accent'}`}
              >
                Brand Settings
              </button>
              <button
                onClick={Object.keys(bgImages).length > 0 ? () => setBgImages({}) : handleGenerateBackgrounds}
                disabled={generatingBgs}
                className={`px-3 py-1 rounded text-xs font-medium border transition-colors ml-auto flex items-center gap-1 ${Object.keys(bgImages).length > 0 ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:bg-accent'}`}
              >
                {generatingBgs
                  ? <><Loader2 className="h-3 w-3 animate-spin" />{bgProgress}/{slides.length}</>
                  : Object.keys(bgImages).length > 0
                    ? <>✕ Remove AI Backgrounds</>
                    : <><Sparkles className="h-3 w-3" /> AI Backgrounds</>}
              </button>
            </div>

            {/* Brand settings panel */}
            {showBrandPanel && (
              <Card className="border-dashed">
                <CardContent className="pt-4 pb-4">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-muted-foreground">BRAND NAME</label>
                      <Input value={brand.logoText} onChange={e => setBrand(b => ({ ...b, logoText: e.target.value }))} placeholder="cloudless" />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-muted-foreground">DOMAIN (without dot)</label>
                      <Input value={brand.domain} onChange={e => setBrand(b => ({ ...b, domain: e.target.value }))} placeholder="gr" />
                    </div>
                    <div className="col-span-2 space-y-1">
                      <label className="text-xs font-medium text-muted-foreground">TAGLINE (shown on cover)</label>
                      <Input value={brand.tagline} onChange={e => setBrand(b => ({ ...b, tagline: e.target.value }))} placeholder="Clear skies. Zero friction." />
                    </div>
                    <div className="col-span-2 flex items-center gap-2">
                      <input
                        type="checkbox"
                        id="showBranding"
                        checked={brand.showBranding}
                        onChange={e => setBrand(b => ({ ...b, showBranding: e.target.checked }))}
                        className="h-4 w-4"
                      />
                      <label htmlFor="showBranding" className="text-xs font-medium">Show branding on slides</label>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Active slide full preview (scaled) */}
            <div className="flex justify-center">
              <div
                style={{
                  transform: `scale(${SLIDE_SCALE})`,
                  transformOrigin: 'top center',
                  width: '1080px',
                  height: '1080px',
                  flexShrink: 0,
                  marginBottom: `-${1080 * (1 - SLIDE_SCALE)}px`,
                }}
              >
                <div ref={el => { slideRefs.current[activeSlide] = el }}>
                  <SlideRenderer slide={slides[activeSlide]} theme={theme} index={activeSlide} total={slides.length} brand={brand} backgroundImageUrl={bgImages[activeSlide]} />
                </div>
              </div>
            </div>

            {/* Slide navigation */}
            <div className="flex items-center justify-center gap-2 pt-2">
              <Button variant="outline" size="icon" onClick={() => setActiveSlide(Math.max(0, activeSlide - 1))} disabled={activeSlide === 0}>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <div className="flex gap-1">
                {slides.map((_, i) => (
                  <button
                    key={i}
                    onClick={() => setActiveSlide(i)}
                    className={`w-2 h-2 rounded-full transition-all ${i === activeSlide ? 'w-5 bg-primary' : 'bg-muted-foreground/40'}`}
                  />
                ))}
              </div>
              <Button variant="outline" size="icon" onClick={() => setActiveSlide(Math.min(slides.length - 1, activeSlide + 1))} disabled={activeSlide === slides.length - 1}>
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>

          {/* Right: Editor panel */}
          <div className="space-y-4">
            <Card>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm">Slide {activeSlide + 1} · <span className="font-normal text-muted-foreground capitalize">{slides[activeSlide].slide_type}</span></CardTitle>
                  <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={() => removeSlide(activeSlide)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground">TITLE</label>
                  <Input value={slides[activeSlide].title} onChange={e => updateSlide(activeSlide, 'title', e.target.value)} />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground">BODY</label>
                  <Textarea value={slides[activeSlide].body} onChange={e => updateSlide(activeSlide, 'body', e.target.value)} rows={3} className="text-sm" />
                </div>
                {(slides[activeSlide].slide_type === 'stat' || slides[activeSlide].slide_type === 'cta' || slides[activeSlide].highlight) && (
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-muted-foreground">HIGHLIGHT / STAT</label>
                    <Input value={slides[activeSlide].highlight || ''} onChange={e => updateSlide(activeSlide, 'highlight', e.target.value)} placeholder="e.g. 73% or Follow for more" />
                  </div>
                )}
                <Select value={slides[activeSlide].slide_type} onValueChange={v => updateSlide(activeSlide, 'slide_type', v)}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cover">Cover</SelectItem>
                    <SelectItem value="content">Content</SelectItem>
                    <SelectItem value="stat">Stat</SelectItem>
                    <SelectItem value="cta">CTA</SelectItem>
                  </SelectContent>
                </Select>
              </CardContent>
            </Card>

            <Button variant="outline" className="w-full" size="sm" onClick={addSlide}>
              <Plus className="mr-2 h-3.5 w-3.5" />Add Slide
            </Button>

            <Card>
              <CardHeader className="pb-3"><CardTitle className="text-sm">Caption</CardTitle></CardHeader>
              <CardContent>
                <Textarea value={caption} onChange={e => setCaption(e.target.value)} rows={4} className="text-xs" />
                <div className="flex flex-wrap gap-1 mt-2">
                  {hashtags.map(h => <Badge key={h} variant="secondary" className="text-xs">#{h}</Badge>)}
                </div>
              </CardContent>
            </Card>

            <Button className="w-full" onClick={() => { setStep('export'); setExportedMediaIds([]) }}>
              Next: Export & Post →
            </Button>
          </div>
        </div>
      )}

      {/* ── Step 3: Export & Post ── */}
      {step === 'export' && (
        <div className="space-y-6">
          {/* Hidden full-res slides for export (off-screen) */}
          <div style={{ position: 'fixed', left: '-9999px', top: 0, pointerEvents: 'none' }}>
            {slides.map((slide, i) => (
              <div key={i} ref={el => { slideRefs.current[i] = el }} style={{ width: '1080px', height: '1080px' }}>
                <SlideRenderer slide={slide} theme={theme} index={i} total={slides.length} brand={brand} backgroundImageUrl={bgImages[i]} />
              </div>
            ))}
          </div>

          <Card>
            <CardHeader><CardTitle>Export Slides as PNG</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Each slide will be rendered at 1080×1080px and uploaded to your media library.
              </p>
              {/* Thumbnail strip */}
              <div className="flex flex-wrap gap-3">
                {slides.map((slide, i) => (
                  <div key={i} className="relative">
                    <div style={{
                      transform: `scale(0.12)`,
                      transformOrigin: 'top left',
                      width: '1080px',
                      height: '1080px',
                      marginBottom: `-${1080 * 0.88}px`,
                      marginRight: `-${1080 * 0.88}px`,
                    }}>
                      <SlideRenderer slide={slide} theme={theme} index={i} total={slides.length} brand={brand} backgroundImageUrl={bgImages[i]} />
                    </div>
                    {exportedMediaIds[i] && (
                      <div className="absolute inset-0 flex items-center justify-center bg-green-500/20 rounded">
                        <Check className="h-5 w-5 text-green-600" />
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="flex gap-3">
                <Button onClick={handleExport} disabled={exporting} className="flex-1">
                  {exporting ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Exporting…</> : <><Download className="mr-2 h-4 w-4" />Export All Slides</>}
                </Button>
                <Button variant="outline" onClick={() => setStep('preview')}>
                  ← Edit
                </Button>
              </div>
            </CardContent>
          </Card>

          {exportedMediaIds.length > 0 && (
            <Card>
              <CardHeader><CardTitle>Publish to Social Platforms</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                {/* Platform account checkboxes */}
                {connectedAccounts.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No connected accounts. Go to Settings → Accounts to connect platforms.</p>
                ) : (
                  <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Select accounts</label>
                    <div className="grid grid-cols-2 gap-2">
                      {connectedAccounts.map(acct => (
                        <label key={acct.id} className="flex items-center gap-2 p-2 rounded border cursor-pointer hover:bg-accent transition-colors">
                          <input
                            type="checkbox"
                            className="h-4 w-4 rounded"
                            checked={selectedPublishPlatforms.includes(acct.platform)}
                            onChange={e => {
                              if (e.target.checked) {
                                setSelectedPublishPlatforms(prev => [...prev, acct.platform])
                              } else {
                                setSelectedPublishPlatforms(prev => prev.filter(p => p !== acct.platform))
                              }
                            }}
                          />
                          <span className="text-sm font-medium capitalize">{acct.platform}</span>
                          <span className="text-xs text-muted-foreground truncate">{acct.display_name || acct.username || ''}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                )}

                {/* Caption preview */}
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Caption</label>
                  <Textarea
                    value={`${caption}\n\n${hashtags.map(h => `#${h}`).join(' ')}`}
                    onChange={e => setCaption(e.target.value)}
                    rows={4}
                    className="text-sm"
                  />
                </div>

                {/* Publish buttons */}
                <div className="flex gap-3">
                  <Button
                    onClick={() => handlePublish(false)}
                    disabled={publishing || selectedPublishPlatforms.length === 0}
                    className="flex-1"
                  >
                    {publishing ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Publishing…</> : <><Send className="mr-2 h-4 w-4" />Publish Now</>}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => handlePublish(true)}
                    disabled={publishing || selectedPublishPlatforms.length === 0}
                    className="flex-1"
                  >
                    Save as Draft
                  </Button>
                </div>
                {selectedPublishPlatforms.includes('instagram') && (
                  <p className="text-xs text-amber-500">Instagram requires public image URLs — slides will publish to other platforms; Instagram will appear as draft until public URLs are configured.</p>
                )}
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
