'use client'

import { useState } from 'react'
import {
  Linkedin, Twitter, Instagram, Facebook, CheckCircle2,
  AlertCircle, ArrowRight,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { useAccounts, useConnectAccount } from '@/hooks/useQueries'
import type { SocialAccount } from '@/types'
import toast from 'react-hot-toast'

function TikTokIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
      <path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-2.88 2.5 2.89 2.89 0 0 1-2.89-2.89 2.89 2.89 0 0 1 2.89-2.89c.28 0 .54.04.79.1V9.01a6.27 6.27 0 0 0-.79-.05 6.34 6.34 0 0 0-6.34 6.34 6.34 6.34 0 0 0 6.34 6.34 6.34 6.34 0 0 0 6.33-6.34V8.69a8.18 8.18 0 0 0 4.78 1.52V6.76a4.85 4.85 0 0 1-1.01-.07z" />
    </svg>
  )
}

interface PlatformOption {
  id: string
  name: string
  icon: React.ComponentType<{ className?: string }>
  color: string
}

const PLATFORMS: PlatformOption[] = [
  { id: 'linkedin', name: 'LinkedIn', icon: Linkedin, color: 'bg-blue-600' },
  { id: 'facebook', name: 'Facebook', icon: Facebook, color: 'bg-blue-700' },
  { id: 'instagram', name: 'Instagram', icon: Instagram, color: 'bg-gradient-to-br from-purple-500 to-pink-500' },
  { id: 'twitter', name: 'Twitter / X', icon: Twitter, color: 'bg-sky-500' },
  { id: 'tiktok', name: 'TikTok', icon: TikTokIcon, color: 'bg-black' },
]

interface StepConnectAccountProps {
  /** Called when the user clicks "Continue" */
  onContinue: () => void
}

export function StepConnectAccount({ onContinue }: StepConnectAccountProps) {
  const [connecting, setConnecting] = useState<string | null>(null)
  const { data: accounts } = useAccounts()
  const connectMutation = useConnectAccount()

  const connectedAccounts: SocialAccount[] = accounts ?? []
  const connectedPlatforms = new Set<string>(connectedAccounts.map(a => a.platform))

  const handleConnect = async (platformId: string) => {
    setConnecting(platformId)
    try {
      const result = await connectMutation.mutateAsync({ platform: platformId, teamId: 'default' })
      const authUrl = (result as { data?: { authorization_url?: string } })?.data?.authorization_url
      if (authUrl) {
        window.location.href = authUrl
      } else {
        toast.success(`${PLATFORMS.find(p => p.id === platformId)?.name} connected!`)
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(
        typeof detail === 'string'
          ? detail
          : 'Failed to connect — make sure OAuth credentials are configured'
      )
    } finally {
      setConnecting(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Connect a social account</CardTitle>
        <CardDescription>
          Link at least one account so you can publish content. You can always add more later.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Connected accounts summary */}
        {connectedAccounts.length > 0 && (
          <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-3">
            <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
              <CheckCircle2 className="h-4 w-4" />
              <span>{connectedAccounts.length} account{connectedAccounts.length === 1 ? '' : 's'} connected</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {connectedAccounts.map(acc => (
                <span
                  key={acc.id}
                  className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs"
                >
                  <CheckCircle2 className="h-3 w-3 text-green-500" />
                  {acc.display_name || acc.username || acc.platform}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Platform buttons */}
        <div className="grid gap-3 sm:grid-cols-2">
          {PLATFORMS.map(platform => {
            const Icon = platform.icon
            const isConnected = connectedPlatforms.has(platform.id)
            return (
              <button
                key={platform.id}
                onClick={() => handleConnect(platform.id)}
                disabled={connecting === platform.id}
                className="flex items-center gap-3 rounded-lg border border-input p-3 text-left transition-colors hover:bg-accent disabled:opacity-50"
              >
                <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${platform.color}`}>
                  <Icon className="h-5 w-5 text-white" />
                </div>
                <div className="flex-1">
                  <p className="text-sm font-medium">{platform.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {isConnected ? 'Connected' : connecting === platform.id ? 'Connecting…' : 'Click to connect'}
                  </p>
                </div>
                {isConnected && <CheckCircle2 className="h-5 w-5 text-green-500" />}
              </button>
            )
          })}
        </div>

        {/* Warning + Continue */}
        {connectedAccounts.length === 0 && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-sm text-amber-600 dark:text-amber-400">
            <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
            <p>You can continue without connecting an account, but you won&apos;t be able to publish posts until you do.</p>
          </div>
        )}

        <div className="flex justify-end pt-2">
          <Button onClick={onContinue}>
            Continue
            <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
