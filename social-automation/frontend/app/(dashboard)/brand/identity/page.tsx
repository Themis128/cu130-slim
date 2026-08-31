'use client'

import { useState, useEffect } from 'react'
import { ArrowLeft, Plus, X } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'
import { useBrand, useUpdateBrand } from '@/hooks/useQueries'

export default function BrandIdentityPage() {
  const { data: brand, isLoading } = useBrand()
  const updateBrand = useUpdateBrand()

  const [name, setName] = useState('')
  const [industry, setIndustry] = useState('')
  const [positioning, setPositioning] = useState('')
  const [mission, setMission] = useState('')
  const [tagline, setTagline] = useState('')
  const [websiteUrl, setWebsiteUrl] = useState('')
  const [values, setValues] = useState<string[]>([])
  const [newValue, setNewValue] = useState('')
  const [competitors, setCompetitors] = useState<string[]>([])
  const [newCompetitor, setNewCompetitor] = useState('')

  useEffect(() => {
    if (brand) {
      setName(brand.name || '')
      setIndustry(brand.industry || '')
      setPositioning(brand.positioning_statement || '')
      setMission(brand.mission || '')
      setTagline(brand.tagline || '')
      setWebsiteUrl(brand.website_url || '')
      setValues(brand.values || [])
      setCompetitors(brand.competitor_names || [])
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
    updateBrand.mutate({
      name,
      industry: industry || undefined,
      positioning_statement: positioning || undefined,
      mission: mission || undefined,
      tagline: tagline || undefined,
      website_url: websiteUrl || undefined,
      values,
      competitor_names: competitors,
    })
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/brand">
          <Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button>
        </Link>
        <h1 className="text-2xl font-bold">Brand Identity</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Basic Information</CardTitle>
          <CardDescription>Your brand&apos;s core identity</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Brand Name</Label>
            <Input id="name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="industry">Industry</Label>
            <Input id="industry" value={industry} onChange={(e) => setIndustry(e.target.value)} placeholder="e.g. Cloud Infrastructure" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="tagline">Tagline</Label>
            <Input id="tagline" value={tagline} onChange={(e) => setTagline(e.target.value)} placeholder="e.g. Clear skies. Zero friction." />
          </div>
          <div className="space-y-2">
            <Label htmlFor="website">Website URL</Label>
            <Input id="website" value={websiteUrl} onChange={(e) => setWebsiteUrl(e.target.value)} placeholder="https://..." />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Strategy</CardTitle>
          <CardDescription>Positioning and mission drive all AI-generated content</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="positioning">Positioning Statement</Label>
            <Textarea
              id="positioning"
              value={positioning}
              onChange={(e) => setPositioning(e.target.value)}
              rows={3}
              placeholder="For [audience] who [need], [brand] is [category] that [benefit]..."
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="mission">Mission</Label>
            <Textarea
              id="mission"
              value={mission}
              onChange={(e) => setMission(e.target.value)}
              rows={3}
              placeholder="Our mission is to..."
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Brand Values</CardTitle>
          <CardDescription>What your brand stands for</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newValue.trim()) {
                  e.preventDefault()
                  setValues([...values, newValue.trim()])
                  setNewValue('')
                }
              }}
              placeholder="Add a value..."
            />
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                if (newValue.trim()) {
                  setValues([...values, newValue.trim()])
                  setNewValue('')
                }
              }}
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {values.map((v, i) => (
              <div key={i} className="flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm">
                {v}
                <button
                  onClick={() => setValues(values.filter((_, idx) => idx !== i))}
                  className="ml-1 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Competitors</CardTitle>
          <CardDescription>Track competitor brands for monitoring</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input
              value={newCompetitor}
              onChange={(e) => setNewCompetitor(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newCompetitor.trim()) {
                  e.preventDefault()
                  setCompetitors([...competitors, newCompetitor.trim()])
                  setNewCompetitor('')
                }
              }}
              placeholder="Add a competitor..."
            />
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                if (newCompetitor.trim()) {
                  setCompetitors([...competitors, newCompetitor.trim()])
                  setNewCompetitor('')
                }
              }}
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {competitors.map((c, i) => (
              <div key={i} className="flex items-center gap-1 rounded-full bg-muted px-3 py-1 text-sm">
                {c}
                <button
                  onClick={() => setCompetitors(competitors.filter((_, idx) => idx !== i))}
                  className="ml-1 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end gap-2">
        <Link href="/brand"><Button variant="outline">Cancel</Button></Link>
        <Button onClick={handleSave} disabled={updateBrand.isPending}>
          {updateBrand.isPending ? 'Saving...' : 'Save Changes'}
        </Button>
      </div>
    </div>
  )
}
