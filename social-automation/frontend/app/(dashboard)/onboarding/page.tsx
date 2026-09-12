'use client'

import { useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { Sparkles, ArrowRight, CheckCircle2, Rocket } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Wizard, type WizardStep } from '@/components/onboarding/Wizard'
import { StepConnectAccount } from '@/components/onboarding/StepConnectAccount'
import { StepBrandBasics } from '@/components/onboarding/StepBrandBasics'
import { StepFirstPost } from '@/components/onboarding/StepFirstPost'
import { useAuth } from '@/hooks/useAuth'
import { useAccounts } from '@/hooks/useQueries'
import { authApi } from '@/services/api'
import toast from 'react-hot-toast'
import { formatErrorToast } from '@/lib/humanizeError'

const COMMON_TIMEZONES = [
  'Europe/Athens',
  'Europe/London',
  'Europe/Paris',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Asia/Tokyo',
  'Australia/Sydney',
  'UTC',
]

export default function OnboardingPage() {
  const router = useRouter()
  const { user, updateProfile, refreshUser } = useAuth()
  const { data: accounts } = useAccounts()

  const [name, setName] = useState(user?.name ?? '')
  const [timezone, setTimezone] = useState(user?.timezone || 'Europe/Athens')
  const [completing, setCompleting] = useState(false)

  const connectedCount = accounts?.length ?? 0

  // ── Step 1: Welcome / profile setup ────────────────────────────────────────
  const handleProfileSave = useCallback(async () => {
    const data: { full_name?: string; timezone?: string } = {}
    if (name.trim() && name !== user?.name) data.full_name = name.trim()
    if (timezone && timezone !== user?.timezone) data.timezone = timezone
    if (Object.keys(data).length > 0) {
      try {
        await updateProfile(data)
      } catch {
        // toast handled inside updateProfile
      }
    }
  }, [name, timezone, user, updateProfile])

  // ── Step 3: Brand basics ───────────────────────────────────────────────────
  const handleBrandSubmit = useCallback(async (data: { brandName: string; industry: string; tone: string }) => {
    // Store brand info in user metadata via profile update (best-effort)
    try {
      await authApi.updateProfile({
        metadata: {
          brand_name: data.brandName,
          industry: data.industry,
          tone: data.tone,
        },
      })
    } catch {
      // Non-fatal — brand data is optional
    }
  }, [])

  // ── Final: mark onboarding complete ────────────────────────────────────────
  const handleComplete = useCallback(async () => {
    setCompleting(true)
    try {
      await updateProfile({ onboarding_completed: true })
      await refreshUser()
      toast.success('You’re set.')
      router.push('/dashboard')
    } catch (err: unknown) {
      toast.error(formatErrorToast('We couldn’t mark onboarding as complete, but you can keep going.', err))
      router.push('/dashboard')
    } finally {
      setCompleting(false)
    }
  }, [updateProfile, refreshUser, router])

  const steps: WizardStep[] = [
    // Step 1 — Welcome / profile
    {
      title: 'Welcome',
      description: 'Set up your profile',
      render: ({ next }) => (
        <Card>
          <CardHeader className="text-center">
            <div className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10">
              <Sparkles className="h-7 w-7 text-primary" />
            </div>
            <CardTitle className="text-2xl">Welcome to SocialAuto</CardTitle>
            <CardDescription>
              Clear skies. Zero friction. Let’s get you set up.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="space-y-1.5">
              <Label htmlFor="onboarding-name">Your name</Label>
              <Input
                id="onboarding-name"
                placeholder="e.g. Jane Doe"
                value={name}
                onChange={e => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="onboarding-timezone">Timezone</Label>
              <select
                id="onboarding-timezone"
                value={timezone}
                onChange={e => setTimezone(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {COMMON_TIMEZONES.map(tz => (
                  <option key={tz} value={tz}>{tz}</option>
                ))}
              </select>
            </div>
            <div className="flex justify-end pt-2">
              <Button
                onClick={async () => {
                  await handleProfileSave()
                  next()
                }}
              >
                Get Started
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      ),
    },
    // Step 2 — Connect account
    {
      title: 'Connect',
      description: 'Connect your channels',
      render: ({ next }) => (
        <StepConnectAccount onContinue={next} />
      ),
    },
    // Step 3 — Brand basics
    {
      title: 'Brand',
      description: 'Add brand basics',
      render: ({ next }) => (
        <StepBrandBasics
          onSubmit={async (data) => {
            await handleBrandSubmit(data)
            next()
          }}
          onSkip={next}
        />
      ),
    },
    // Step 4 — Create first post
    {
      title: 'First post',
      description: 'Save a first draft',
      render: ({ next }) => (
        <StepFirstPost onContinue={next} />
      ),
    },
    // Step 5 — Done
    {
      title: 'Done',
      description: 'You&apos;re all set!',
      render: () => (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 text-center">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-green-500/10">
              <Rocket className="h-8 w-8 text-green-500" />
            </div>
            <h2 className="text-2xl font-bold">You&apos;re all set!</h2>
            <p className="mt-2 max-w-md text-muted-foreground">
              Setup is complete. Here’s what you can do next:
            </p>

            {/* Summary */}
            <div className="mt-6 w-full max-w-sm space-y-2 text-left">
              <SummaryRow
                done={name.trim().length > 0}
                label="Profile set up"
              />
              <SummaryRow
                done={connectedCount > 0}
                label={`${connectedCount} channel${connectedCount === 1 ? '' : 's'} connected`}
              />
              <SummaryRow
                done={true}
                label="Brand basics configured"
              />
              <SummaryRow
                done={true}
                label="Ready to create posts"
              />
            </div>

            <Button
              onClick={handleComplete}
              isLoading={completing}
              size="lg"
              className="mt-8"
            >
              Go to Dashboard
              <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </CardContent>
        </Card>
      ),
    },
  ]

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full">
        <Wizard steps={steps} onComplete={handleComplete} />
      </div>
    </div>
  )
}

function SummaryRow({ done, label }: { done: boolean; label: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border p-3">
      <CheckCircle2
        className={done ? 'h-5 w-5 text-green-500' : 'h-5 w-5 text-muted-foreground'}
      />
      <span className="text-sm">{label}</span>
    </div>
  )
}
