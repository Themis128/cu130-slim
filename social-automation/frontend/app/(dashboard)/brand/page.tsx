'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Palette, Type, Image as ImageIcon, BookOpen, Plus, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useBrand, useCreateBrand } from '@/hooks/useQueries'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'

export default function BrandPage() {
  const { data: brand, isLoading } = useBrand()
  const createBrand = useCreateBrand()
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState('')
  const [industry, setIndustry] = useState('')
  const [tagline, setTagline] = useState('')
  const [websiteUrl, setWebsiteUrl] = useState('')

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <p className="text-muted-foreground">Loading brand...</p>
      </div>
    )
  }

  // No brand yet — show onboarding
  if (!brand) {
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
                <Button className="w-full" size="lg" onClick={() => setShowCreate(true)}>
                  <Plus className="mr-2 h-5 w-5" />
                  Create Brand Manually
                </Button>
                <div className="text-center text-sm text-muted-foreground">
                  or use the AI Brand Kit Extractor (coming in Phase 2)
                </div>
              </div>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  createBrand.mutate({
                    name,
                    industry: industry || undefined,
                    tagline: tagline || undefined,
                    website_url: websiteUrl || undefined,
                  })
                }}
                className="space-y-4"
              >
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
                  <Input id="website" value={websiteUrl} onChange={(e) => setWebsiteUrl(e.target.value)} placeholder="https://cloudless.gr" />
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
            <span className="text-green-600 text-xs">✓ Complete</span>
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
