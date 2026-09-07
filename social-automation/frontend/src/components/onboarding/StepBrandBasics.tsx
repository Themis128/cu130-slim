'use client'

import { useState } from 'react'
import { ArrowRight, SkipForward } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { cn } from '@/lib/utils'

const INDUSTRIES = [
  'Technology',
  'E-commerce',
  'Healthcare',
  'Finance',
  'Education',
  'Marketing',
  'Other',
] as const

const TONES = [
  'Professional',
  'Casual',
  'Friendly',
  'Authoritative',
] as const

interface StepBrandBasicsProps {
  /** Called with the brand data when the user clicks "Continue" */
  onSubmit: (data: { brandName: string; industry: string; tone: string }) => void
  /** Called when the user clicks "Skip for now" */
  onSkip: () => void
}

export function StepBrandBasics({ onSubmit, onSkip }: StepBrandBasicsProps) {
  const [brandName, setBrandName] = useState('')
  const [industry, setIndustry] = useState<string>('')
  const [tone, setTone] = useState<string>('Professional')

  const handleSubmit = () => {
    onSubmit({ brandName, industry, tone })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Brand basics</CardTitle>
        <CardDescription>
          Tell us about your brand so we can tailor content suggestions.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Brand name */}
        <div className="space-y-1.5">
          <Label htmlFor="brand-name">Brand name</Label>
          <Input
            id="brand-name"
            placeholder="e.g. Acme Inc."
            value={brandName}
            onChange={e => setBrandName(e.target.value)}
          />
        </div>

        {/* Industry */}
        <div className="space-y-1.5">
          <Label htmlFor="industry">Industry</Label>
          <select
            id="industry"
            value={industry}
            onChange={e => setIndustry(e.target.value)}
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            <option value="">Select an industry…</option>
            {INDUSTRIES.map(ind => (
              <option key={ind} value={ind}>{ind}</option>
            ))}
          </select>
        </div>

        {/* Voice / tone */}
        <div className="space-y-1.5">
          <Label>Voice / tone</Label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {TONES.map(t => (
              <button
                key={t}
                type="button"
                onClick={() => setTone(t)}
                className={cn(
                  'rounded-lg border px-3 py-2 text-sm transition-colors',
                  tone === t
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-input hover:bg-accent'
                )}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between pt-2">
          <Button variant="ghost" onClick={onSkip}>
            <SkipForward className="mr-2 h-4 w-4" />
            Skip for now
          </Button>
          <Button onClick={handleSubmit} disabled={!brandName.trim()}>
            Continue
            <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
