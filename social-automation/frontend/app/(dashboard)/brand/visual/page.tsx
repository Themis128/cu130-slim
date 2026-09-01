'use client'

import { useState, useEffect } from 'react'
import { ArrowLeft, Plus, X, Sparkles, Loader2, Upload } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'
import { useBrand, useUpdateBrandVisual } from '@/hooks/useQueries'
import { brandApi, mediaUrl } from '@/services/api'
import toast from 'react-hot-toast'

const LOGO_STYLES = [
  { value: 'modern minimalist', label: 'Modern Minimalist' },
  { value: 'geometric', label: 'Geometric' },
  { value: 'abstract', label: 'Abstract' },
  { value: 'lettermark', label: 'Lettermark' },
  { value: 'wordmark', label: 'Wordmark' },
  { value: 'emblem', label: 'Emblem' },
  { value: 'mascot', label: 'Mascot' },
  { value: 'combination', label: 'Combination' },
]

export default function BrandVisualPage() {
  const { data: brand, isLoading } = useBrand()
  const updateVisual = useUpdateBrandVisual()

  const [primaryColor, setPrimaryColor] = useState('#0b1220')
  const [accentColor, setAccentColor] = useState('#22d3e6')
  const [neutralColors, setNeutralColors] = useState<string[]>([])
  const [newNeutral, setNewNeutral] = useState('#64748B')
  const [fontHeading, setFontHeading] = useState('')
  const [fontBody, setFontBody] = useState('')
  const [logoUrl, setLogoUrl] = useState('')
  const [faviconUrl, setFaviconUrl] = useState('')
  const [imageStyle, setImageStyle] = useState('')
  const [photographyDirection, setPhotographyDirection] = useState('')

  // AI logo generation state
  const [logoDescription, setLogoDescription] = useState('')
  const [logoStyle, setLogoStyle] = useState('modern minimalist')
  const [generatingLogo, setGeneratingLogo] = useState(false)
  const [generatingFavicon, setGeneratingFavicon] = useState(false)

  useEffect(() => {
    if (brand?.visual) {
      setPrimaryColor(brand.visual.primary_color || '#0b1220')
      setAccentColor(brand.visual.accent_color || '#22d3e6')
      setNeutralColors(brand.visual.neutral_colors || [])
      setFontHeading(brand.visual.font_heading || '')
      setFontBody(brand.visual.font_body || '')
      setLogoUrl(brand.visual.logo_url || '')
      const variants = brand.visual.logo_variants || {}
      setFaviconUrl(variants.favicon || '')
      setImageStyle(brand.visual.image_style || '')
      setPhotographyDirection(brand.visual.photography_direction || '')
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
    updateVisual.mutate({
      primary_color: primaryColor,
      accent_color: accentColor,
      neutral_colors: neutralColors,
      font_heading: fontHeading || undefined,
      font_body: fontBody || undefined,
      logo_url: logoUrl || undefined,
      image_style: imageStyle || undefined,
      photography_direction: photographyDirection || undefined,
    })
  }

  const handleGenerateLogo = async () => {
    setGeneratingLogo(true)
    try {
      const res = await brandApi.generateLogo({
        description: logoDescription,
        style: logoStyle,
      })
      const data = res.data as { logo_url: string; asset_id: string; prompt: string }
      setLogoUrl(data.logo_url)
      toast.success('Logo generated and saved to brand!')
    } catch {
      toast.error('Failed to generate logo')
    } finally {
      setGeneratingLogo(false)
    }
  }

  const handleGenerateFavicon = async () => {
    if (!logoUrl) {
      toast.error('Generate or set a logo first')
      return
    }
    setGeneratingFavicon(true)
    try {
      const res = await brandApi.generateFavicon()
      const data = res.data as { favicon_url: string; asset_id: string }
      setFaviconUrl(data.favicon_url)
      toast.success('Favicon generated and saved to brand!')
    } catch {
      toast.error('Failed to generate favicon')
    } finally {
      setGeneratingFavicon(false)
    }
  }

  // Resolve logo URL for preview — media view paths need the full API base
  const resolveLogoUrl = (url: string) => {
    if (!url) return ''
    if (url.startsWith('http')) return url
    if (url.startsWith('/api/v1/media/view/')) {
      return `${process.env.NEXT_PUBLIC_API_URL || ''}${url}`
    }
    return mediaUrl(url)
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/brand">
          <Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button>
        </Link>
        <h1 className="text-2xl font-bold">Visual Identity</h1>
      </div>

      {/* Color Palette */}
      <Card>
        <CardHeader>
          <CardTitle>Color Palette</CardTitle>
          <CardDescription>Brand colors used in AI image generation and carousels</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Primary Color</Label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={primaryColor}
                  onChange={(e) => setPrimaryColor(e.target.value)}
                  className="h-10 w-16 rounded border cursor-pointer"
                />
                <Input value={primaryColor} onChange={(e) => setPrimaryColor(e.target.value)} className="flex-1" />
              </div>
              <div className="h-12 rounded-lg" style={{ backgroundColor: primaryColor }} />
            </div>
            <div className="space-y-2">
              <Label>Accent Color</Label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={accentColor}
                  onChange={(e) => setAccentColor(e.target.value)}
                  className="h-10 w-16 rounded border cursor-pointer"
                />
                <Input value={accentColor} onChange={(e) => setAccentColor(e.target.value)} className="flex-1" />
              </div>
              <div className="h-12 rounded-lg" style={{ backgroundColor: accentColor }} />
            </div>
          </div>

          <div className="space-y-2">
            <Label>Neutral Colors</Label>
            <div className="flex gap-2">
              <input
                type="color"
                value={newNeutral}
                onChange={(e) => setNewNeutral(e.target.value)}
                className="h-10 w-16 rounded border cursor-pointer"
              />
              <Button type="button" variant="outline" onClick={() => {
                if (!neutralColors.includes(newNeutral)) setNeutralColors([...neutralColors, newNeutral])
              }}>
                <Plus className="h-4 w-4" /> Add
              </Button>
            </div>
            <div className="flex flex-wrap gap-2">
              {neutralColors.map((c, i) => (
                <div key={i} className="flex items-center gap-1">
                  <div className="h-10 w-10 rounded border" style={{ backgroundColor: c }} />
                  <button onClick={() => setNeutralColors(neutralColors.filter((_, idx) => idx !== i))}>
                    <X className="h-4 w-4 text-muted-foreground" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Typography */}
      <Card>
        <CardHeader>
          <CardTitle>Typography</CardTitle>
          <CardDescription>Fonts used in generated visuals</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="font-heading">Heading Font</Label>
            <Input id="font-heading" value={fontHeading} onChange={(e) => setFontHeading(e.target.value)} placeholder="e.g. Inter, Helvetica Neue" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="font-body">Body Font</Label>
            <Input id="font-body" value={fontBody} onChange={(e) => setFontBody(e.target.value)} placeholder="e.g. Inter, Arial" />
          </div>
        </CardContent>
      </Card>

      {/* Logo — AI Generation + URL */}
      <Card>
        <CardHeader>
          <CardTitle>Logo</CardTitle>
          <CardDescription>Generate a logo with AI or enter a URL to your existing logo</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Current logo preview */}
          {logoUrl && (
            <div className="rounded-lg border p-6 flex items-center justify-center bg-muted/30">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={resolveLogoUrl(logoUrl)} alt="Brand logo" className="max-h-24 max-w-xs" />
            </div>
          )}

          {/* AI Logo Generator */}
          <div className="rounded-lg border-2 border-primary/20 bg-primary/5 p-4 space-y-3">
            <div className="flex items-center gap-2 text-primary">
              <Sparkles className="h-5 w-5" />
              <span className="font-medium">AI Logo Generator</span>
            </div>
            <p className="text-sm text-muted-foreground">
              Generate a professional logo using your brand name, colors, and industry. The result is saved to your media library and set as your brand logo automatically.
            </p>
            <div className="space-y-2">
              <Label htmlFor="logo-style">Logo Style</Label>
              <select
                id="logo-style"
                value={logoStyle}
                onChange={(e) => setLogoStyle(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                {LOGO_STYLES.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="logo-desc">Description (optional)</Label>
              <Textarea
                id="logo-desc"
                value={logoDescription}
                onChange={(e) => setLogoDescription(e.target.value)}
                rows={2}
                placeholder="e.g. A cloud with a checkmark, representing clear skies and reliability"
              />
            </div>
            <Button
              type="button"
              onClick={handleGenerateLogo}
              disabled={generatingLogo}
              className="w-full"
            >
              {generatingLogo ? (
                <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Generating logo...</>
              ) : (
                <><Sparkles className="mr-2 h-4 w-4" /> Generate Logo with AI</>
              )}
            </Button>
          </div>

          {/* Manual URL input */}
          <div className="space-y-2">
            <Label htmlFor="logo-url">Or enter Logo URL manually</Label>
            <Input id="logo-url" value={logoUrl} onChange={(e) => setLogoUrl(e.target.value)} placeholder="https://..." />
          </div>
        </CardContent>
      </Card>

      {/* Favicon — AI Generation from Logo */}
      <Card>
        <CardHeader>
          <CardTitle>Favicon</CardTitle>
          <CardDescription>Generate a favicon icon from your brand logo with AI</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Current favicon preview */}
          {faviconUrl && (
            <div className="flex items-center gap-4">
              <div className="rounded-lg border p-3 flex items-center justify-center bg-muted/30">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={resolveLogoUrl(faviconUrl)} alt="Brand favicon" className="h-16 w-16" />
              </div>
              <div className="text-sm text-muted-foreground">
                Favicon saved to media library and brand variants.
              </div>
            </div>
          )}

          {/* AI Favicon Generator */}
          <div className="rounded-lg border-2 border-primary/20 bg-primary/5 p-4 space-y-3">
            <div className="flex items-center gap-2 text-primary">
              <Sparkles className="h-5 w-5" />
              <span className="font-medium">AI Favicon Generator</span>
            </div>
            <p className="text-sm text-muted-foreground">
              Generates a simplified square icon from your brand logo, optimized for use as a browser favicon. Requires a logo to be set first.
            </p>
            <Button
              type="button"
              onClick={handleGenerateFavicon}
              disabled={generatingFavicon || !logoUrl}
              className="w-full"
            >
              {generatingFavicon ? (
                <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Generating favicon...</>
              ) : (
                <><Sparkles className="mr-2 h-4 w-4" /> Generate Favicon from Logo</>
              )}
            </Button>
            {!logoUrl && (
              <p className="text-xs text-muted-foreground text-center">
                Generate or set a logo above first.
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Image Style */}
      <Card>
        <CardHeader>
          <CardTitle>Image Style</CardTitle>
          <CardDescription>Direction for AI-generated images</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="image-style">Image Style Description</Label>
            <Textarea
              id="image-style"
              value={imageStyle}
              onChange={(e) => setImageStyle(e.target.value)}
              rows={3}
              placeholder="e.g. Minimalist, dark background, neon accents, futuristic, clean lines"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="photo-direction">Photography Direction</Label>
            <Textarea
              id="photo-direction"
              value={photographyDirection}
              onChange={(e) => setPhotographyDirection(e.target.value)}
              rows={3}
              placeholder="e.g. Professional product shots on dark backgrounds, abstract tech imagery"
            />
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end gap-2">
        <Link href="/brand"><Button variant="outline">Cancel</Button></Link>
        <Button onClick={handleSave} disabled={updateVisual.isPending}>
          {updateVisual.isPending ? 'Saving...' : 'Save Visual'}
        </Button>
      </div>
    </div>
  )
}
