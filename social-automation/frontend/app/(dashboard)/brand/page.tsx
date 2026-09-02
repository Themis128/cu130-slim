'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Palette, Plus, Sparkles, Loader2, Wand2, Type, Image as ImageIcon, BookOpen, Package } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useBrand, useCreateBrand } from '@/hooks/useQueries'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { brandApi } from '@/services/api'
import toast from 'react-hot-toast'

interface ExtractedBrandKit {
  name?: string
  industry?: string
  tagline?: string
  website_url?: string
  positioning_statement?: string
  mission?: string
  values?: string[]
  target_audience?: { demographics?: string; pain_points?: string; goals?: string }
  competitor_names?: string[]
  voice?: {
    tone_dimensions?: Record<string, number>
    messaging_pillars?: Array<{ pillar: string; description: string }>
    banned_phrases?: string[]
    preferred_phrases?: string[]
    example_content?: string
    voice_signature?: Record<string, unknown>
  }
  visual?: {
    primary_color?: string
    accent_color?: string
    neutral_colors?: string[]
    font_heading?: string
    font_body?: string
    logo_url?: string
    image_style?: string
    photography_direction?: string
  }
}

export default function BrandPage() {
  const { data: brand, isLoading } = useBrand()
  const createBrand = useCreateBrand()
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState('')
  const [industry, setIndustry] = useState('')
  const [tagline, setTagline] = useState('')
  const [websiteUrl, setWebsiteUrl] = useState('')

  // AI Brand Kit Extractor state
  const [extractUrl, setExtractUrl] = useState('')
  const [extracting, setExtracting] = useState(false)
  const [extractedKit, setExtractedKit] = useState<ExtractedBrandKit | null>(null)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <p className="text-muted-foreground">Loading brand...</p>
      </div>
    )
  }

  // No brand yet — show onboarding
  if (!brand) {
    const handleExtract = async () => {
      if (!extractUrl.trim()) {
        toast.error('Enter your website URL')
        return
      }
      setExtracting(true)
      try {
        const res = await brandApi.extractFromUrl({ url: extractUrl.trim() })
        const kit = res.data as ExtractedBrandKit
        setExtractedKit(kit)
        // Pre-fill the manual form with extracted data
        setName(kit.name || '')
        setIndustry(kit.industry || '')
        setTagline(kit.tagline || '')
        setWebsiteUrl(kit.website_url || extractUrl.trim())
        setShowCreate(true)
        toast.success('Brand kit extracted! Review and edit the fields below.')
      } catch {
        toast.error('Failed to extract brand kit. Try creating manually.')
      } finally {
        setExtracting(false)
      }
    }

    const handleCreateWithExtracted = () => {
      if (!extractedKit || !name) return
      createBrand.mutate({
        name,
        industry: industry || undefined,
        tagline: tagline || undefined,
        website_url: websiteUrl || undefined,
        positioning_statement: extractedKit.positioning_statement || undefined,
        mission: extractedKit.mission || undefined,
        values: extractedKit.values,
        target_audience: extractedKit.target_audience,
        competitor_names: extractedKit.competitor_names,
      })
    }

    return (
      <div className="max-w-2xl mx-auto py-12">
        <Card>
          <CardHeader className="text-center">
            <div className="flex justify-center mb-4">
              <div className="h-16 w-16 rounded-full bg-primary/10 flex items-center justify-center">
                <Palette className="h-8 w-8 text-primary" />
              </div>
            </div>
            <CardTitle className="text-2xl">Create Your Brand Identity</CardTitle>
            <CardDescription>
              Define your brand DNA, voice, and visual identity. Every AI-generated post will be on-brand.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!showCreate ? (
              <div className="space-y-4">
                {/* AI Brand Kit Extractor */}
                <div className="rounded-lg border-2 border-primary/20 bg-primary/5 p-4 space-y-3">
                  <div className="flex items-center gap-2 text-primary">
                    <Wand2 className="h-5 w-5" />
                    <span className="font-medium">AI Brand Kit Extractor</span>
                  </div>
                  <p className="text-sm text-muted-foreground">
                    Enter your website URL and AI will extract your brand identity, voice, and visual style automatically.
                  </p>
                  <div className="flex gap-2">
                    <Input
                      value={extractUrl}
                      onChange={(e) => setExtractUrl(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && extractUrl.trim()) {
                          e.preventDefault()
                          handleExtract()
                        }
                      }}
                      placeholder="https://cloudless.gr"
                    />
                    <Button
                      onClick={handleExtract}
                      disabled={extracting || !extractUrl.trim()}
                    >
                      {extracting ? (
                        <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Extracting...</>
                      ) : (
                        <><Sparkles className="mr-2 h-4 w-4" /> Extract</>
                      )}
                    </Button>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <div className="h-px bg-border flex-1" />
                  <span className="text-xs text-muted-foreground">OR</span>
                  <div className="h-px bg-border flex-1" />
                </div>

                <Button className="w-full" size="lg" variant="outline" onClick={() => setShowCreate(true)}>
                  <Plus className="mr-2 h-5 w-5" />
                  Create Brand Manually
                </Button>

                <Link href="/brand/onboarding" className="block">
                  <Button className="w-full" size="lg" variant="secondary">
                    <Wand2 className="mr-2 h-5 w-5" />
                    Use Brand Kit Wizard (3 steps)
                  </Button>
                </Link>
              </div>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  if (extractedKit) {
                    handleCreateWithExtracted()
                  } else {
                    createBrand.mutate({
                      name,
                      industry: industry || undefined,
                      tagline: tagline || undefined,
                      website_url: websiteUrl || undefined,
                    })
                  }
                }}
                className="space-y-4"
              >
                {extractedKit && (
                  <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-3 text-sm">
                    <div className="flex items-center gap-2 text-green-700 mb-1">
                      <Sparkles className="h-4 w-4" />
                      <span className="font-medium">Extracted from {extractUrl}</span>
                    </div>
                    <p className="text-muted-foreground">
                      Fields below are pre-filled from your website. Edit anything that needs adjusting.
                      After creating the brand, visit the Voice and Visual pages to review the extracted details.
                    </p>
                  </div>
                )}
                <div className="space-y-2">
                  <Label htmlFor="name">Brand Name *</Label>
                  <Input id="name" value={name} onChange={(e) => setName(e.target.value)} required placeholder="Cloudless" />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="industry">Industry</Label>
                  <Input id="industry" value={industry} onChange={(e) => setIndustry(e.target.value)} placeholder="Cloud Infrastructure" />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="tagline">Tagline</Label>
                  <Input id="tagline" value={tagline} onChange={(e) => setTagline(e.target.value)} placeholder="Clear skies. Zero friction." />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="website">Website URL</Label>
                  <Input id="website" value={websiteUrl} onChange={(e) => setWebsiteUrl(e.target.value)} placeholder="https://..." />
                </div>
                <div className="flex gap-2">
                  <Button type="submit" disabled={createBrand.isPending || !name} className="flex-1">
                    {createBrand.isPending ? 'Creating...' : 'Create Brand'}
                  </Button>
                  <Button type="button" variant="outline" onClick={() => setShowCreate(false)}>
                    Cancel
                  </Button>
                </div>
              </form>
            )}
          </CardContent>
        </Card>
      </div>
    )
  }

  // Brand exists — show dashboard
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">{brand.name}</h1>
          {brand.tagline && <p className="text-muted-foreground mt-1">{brand.tagline}</p>}
        </div>
        {brand.industry && <Badge variant="secondary">{brand.industry}</Badge>}
      </div>

      {/* Brand completeness */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-primary" />
            Brand Completeness
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <CompletenessCard label="Identity" filled={!!brand.positioning_statement} href="/brand/identity" />
            <CompletenessCard label="Voice" filled={!!brand.voice?.tone_dimensions && Object.keys(brand.voice.tone_dimensions).length > 0} href="/brand/voice" />
            <CompletenessCard label="Visual" filled={!!brand.visual?.primary_color} href="/brand/visual" />
            <CompletenessCard label="Guidelines" filled={!!brand.guidelines} href="/brand/guidelines" />
            <CompletenessCard label="Assets" filled={!!brand.assets && brand.assets.length > 0} href="/brand/assets" />
          </div>
        </CardContent>
      </Card>

      {/* Quick links */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <BrandLinkCard
          title="Brand Identity"
          description="Positioning, mission, values, audience"
          icon={<Palette className="h-5 w-5" />}
          href="/brand/identity"
        />
        <BrandLinkCard
          title="Voice & Tone"
          description="Tone dimensions, banned phrases, messaging pillars"
          icon={<Type className="h-5 w-5" />}
          href="/brand/voice"
        />
        <BrandLinkCard
          title="Visual Identity"
          description="Colors, fonts, logo, image style"
          icon={<ImageIcon className="h-5 w-5" />}
          href="/brand/visual"
        />
        <BrandLinkCard
          title="Brand Guidelines"
          description="Compiled guidelines, shareable link"
          icon={<BookOpen className="h-5 w-5" />}
          href="/brand/guidelines"
        />
        <BrandLinkCard
          title="Brand Assets"
          description="Logos, templates, OG images, favicons"
          icon={<Package className="h-5 w-5" />}
          href="/brand/assets"
        />
      </div>
    </div>
  )
}

function CompletenessCard({ label, filled, href }: { label: string; filled: boolean; href: string }) {
  return (
    <Link href={href}>
      <div className={`rounded-lg border p-4 transition-colors hover:bg-accent ${filled ? 'border-green-500/30 bg-green-500/5' : 'border-muted'}`}>
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">{label}</span>
          {filled ? (
            <span className="text-green-600 text-xs">&#10003; Complete</span>
          ) : (
            <span className="text-muted-foreground text-xs">Pending</span>
          )}
        </div>
      </div>
    </Link>
  )
}

function BrandLinkCard({ title, description, icon, href }: { title: string; description: string; icon: React.ReactNode; href: string }) {
  return (
    <Link href={href}>
      <Card className="hover:border-primary/50 transition-colors cursor-pointer">
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center text-primary">
              {icon}
            </div>
            <div>
              <CardTitle className="text-base">{title}</CardTitle>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{description}</p>
        </CardContent>
      </Card>
    </Link>
  )
}
