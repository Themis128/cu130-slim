'use client'

import { useState } from 'react'
import {
  Linkedin, Twitter, Instagram, Facebook, Hash,
  CheckCircle2, AlertCircle, Loader2, Trash2, ExternalLink,
  ChevronDown, ChevronRight, Copy, Settings2, BookOpen,
  ShieldCheck, ShieldAlert, ShieldX, Clock, RefreshCw,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Separator } from '@/components/ui/Separator'
import { Skeleton } from '@/components/ui/Skeleton'
import { useAccounts, useConnectAccount, useDisconnectAccount, useScheduledPosts } from '@/hooks/useQueries'
import type { SocialAccount, Post, PostTarget } from '@/types'
import toast from 'react-hot-toast'
import Link from 'next/link'

// ── platform metadata ─────────────────────────────────────────────────────────

interface Step {
  text: string
  note?: string
  code?: string
}

interface PlatformSetup {
  id: string
  name: string
  icon: React.ComponentType<{ className?: string }>
  color: string
  textColor: string
  description: string
  devPortalUrl: string
  scopes: string[]
  envVars: string[]
  steps: Step[]
}

const platforms: PlatformSetup[] = [
  {
    id: 'linkedin',
    name: 'LinkedIn',
    icon: Linkedin,
    color: 'bg-blue-600',
    textColor: 'text-blue-600',
    description: 'Professional network for B2B content',
    devPortalUrl: 'https://www.linkedin.com/developers/apps',
    scopes: ['r_liteprofile', 'r_emailaddress', 'w_member_social'],
    envVars: ['LINKEDIN_CLIENT_ID', 'LINKEDIN_CLIENT_SECRET'],
    steps: [
      {
        text: 'Go to the LinkedIn Developer Portal and sign in with your LinkedIn account.',
        code: 'https://www.linkedin.com/developers/apps',
      },
      {
        text: 'Click "Create App". Fill in App Name (e.g. "SocialAuto"), attach a LinkedIn Page, and upload a logo.',
        note: 'You must have a LinkedIn Page — create one for free if you don\'t have one.',
      },
      {
        text: 'Open the "Auth" tab of your new app. Copy the Client ID and Client Secret.',
        note: 'Click "Generate" next to Client Secret if it\'s empty.',
      },
{
	        text: 'Under "OAuth 2.0 Settings", add this Redirect URL:',
	        code: 'http://localhost:8000/api/v1/auth/oauth/linkedin/callback',
	      },
      {
        text: 'Open the "Products" tab and request both "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect".',
        note: 'Approval is instant for most products.',
      },
      {
        text: 'Save LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in the Env Manager, then restart the API.',
      },
    ],
  },
  {
    id: 'twitter',
    name: 'Twitter / X',
    icon: Twitter,
    color: 'bg-sky-500',
    textColor: 'text-sky-500',
    description: 'Real-time conversations and threads',
    devPortalUrl: 'https://developer.twitter.com/en/portal/dashboard',
    scopes: ['tweet.read', 'tweet.write', 'users.read', 'offline.access'],
    envVars: ['TWITTER_CLIENT_ID', 'TWITTER_CLIENT_SECRET', 'TWITTER_BEARER_TOKEN'],
    steps: [
      {
        text: 'Go to the Twitter Developer Portal. You need a developer account — apply if this is your first time.',
        code: 'https://developer.twitter.com/en/portal/dashboard',
      },
      {
        text: 'Create a Project (e.g. "SocialAuto"), then create an App inside that project.',
      },
      {
        text: 'Under "User authentication settings" → enable OAuth 2.0. Set App permissions to "Read and Write". Set Type of App to "Web App".',
      },
{
	        text: 'Add this Callback URI and Website URL:',
	        code: 'http://localhost:8083/api/v1/auth/oauth/twitter/callback',
	      },
      {
        text: 'Go to "Keys and Tokens". Copy OAuth 2.0 Client ID → TWITTER_CLIENT_ID, and Client Secret → TWITTER_CLIENT_SECRET.',
      },
      {
        text: 'Also copy the Bearer Token → TWITTER_BEARER_TOKEN (used for read-only API access in n8n workflows).',
        note: 'Generate a new one if it\'s never been revealed.',
      },
      {
        text: 'Save all three vars in the Env Manager, then restart the API.',
      },
    ],
  },
  {
    id: 'instagram',
    name: 'Instagram',
    icon: Instagram,
    color: 'bg-gradient-to-br from-purple-500 to-pink-500',
    textColor: 'text-pink-500',
    description: 'Visual content and Reels',
    devPortalUrl: 'https://developers.facebook.com/apps',
    scopes: ['instagram_basic', 'instagram_content_publish', 'pages_show_list'],
    envVars: ['INSTAGRAM_CLIENT_ID', 'INSTAGRAM_CLIENT_SECRET'],
    steps: [
      {
        text: 'Instagram publishing uses the Facebook / Meta developer platform. Go to Meta for Developers.',
        code: 'https://developers.facebook.com/apps',
      },
      {
        text: 'Click "Create App". Choose "Other" → "Business" type. Give it a name and attach your Meta Business account.',
      },
      {
        text: 'On the app dashboard, click "Add Product" → add "Instagram Graph API".',
      },
      {
        text: 'Go to Settings → Basic. Copy App ID → INSTAGRAM_CLIENT_ID, and App Secret → INSTAGRAM_CLIENT_SECRET.',
        note: 'Click "Show" next to App Secret — you\'ll need to re-enter your password.',
      },
{
	        text: 'Under Instagram Graph API → Settings, add this redirect URI:',
	        code: 'http://localhost:8083/api/v1/auth/oauth/instagram/callback',
	      },
      {
        text: 'Connect your Instagram Business or Creator account to your Facebook Page (required for API access).',
        note: 'Personal Instagram accounts cannot publish via API. Switch to a Creator or Business profile in the Instagram app.',
      },
      {
        text: 'Save INSTAGRAM_CLIENT_ID and INSTAGRAM_CLIENT_SECRET in the Env Manager, then restart the API.',
      },
    ],
  },
  {
    id: 'facebook',
    name: 'Facebook',
    icon: Facebook,
    color: 'bg-blue-700',
    textColor: 'text-blue-700',
    description: 'Pages and Groups management',
    devPortalUrl: 'https://developers.facebook.com/apps',
    scopes: ['pages_read_engagement', 'pages_manage_posts', 'pages_show_list'],
    envVars: ['FACEBOOK_CLIENT_ID', 'FACEBOOK_CLIENT_SECRET'],
    steps: [
      {
        text: 'Go to Meta for Developers (same portal as Instagram).',
        code: 'https://developers.facebook.com/apps',
      },
      {
        text: 'You can reuse the same app you created for Instagram, or create a new one. Either way, add the "Facebook Login" product.',
      },
{
	        text: 'Under Facebook Login → Settings, add this valid OAuth Redirect URI:',
	        code: 'http://localhost:8083/api/v1/auth/oauth/facebook/callback',
	      },
      {
        text: 'Go to Settings → Basic. Copy App ID → FACEBOOK_CLIENT_ID and App Secret → FACEBOOK_CLIENT_SECRET.',
      },
      {
        text: 'Add your Facebook Page in the app\'s "Pages" section so it can post on your behalf.',
        note: 'You must be an admin of the Page.',
      },
      {
        text: 'Save FACEBOOK_CLIENT_ID and FACEBOOK_CLIENT_SECRET in the Env Manager, then restart the API.',
      },
    ],
  },
  {
    id: 'threads',
    name: 'Threads',
    icon: Hash,
    color: 'bg-gray-900',
    textColor: 'text-gray-900 dark:text-gray-100',
    description: 'Text-based conversations by Meta',
    devPortalUrl: 'https://developers.facebook.com/apps',
    scopes: ['threads_basic', 'threads_content_publish'],
    envVars: ['THREADS_CLIENT_ID', 'THREADS_CLIENT_SECRET'],
    steps: [
      {
        text: 'Threads uses the same Meta developer platform as Instagram and Facebook.',
        code: 'https://developers.facebook.com/apps',
      },
      {
        text: 'Open your existing Meta app (or create a new one). Click "Add Product" → search for "Threads API" and click "Set Up".',
        note: 'The Threads API was made public in 2024 — you may need to accept updated terms.',
      },
      {
        text: 'Under Threads API → Quickstart → add your Threads / Instagram account as a test user.',
      },
{
	        text: 'Add this redirect URI under Threads API → Settings:',
	        code: 'http://localhost:8083/api/v1/auth/oauth/threads/callback',
	      },
      {
        text: 'Go to Settings → Basic. Copy App ID → THREADS_CLIENT_ID and App Secret → THREADS_CLIENT_SECRET.',
        note: 'These may be the same values as your Facebook/Instagram app if you added Threads to the same app.',
      },
      {
        text: 'Save THREADS_CLIENT_ID and THREADS_CLIENT_SECRET in the Env Manager, then restart the API.',
      },
    ],
  },
]

const platformColors: Record<string, string> = {
  linkedin: 'bg-blue-600',
  twitter: 'bg-sky-500',
  instagram: 'bg-pink-500',
  facebook: 'bg-blue-700',
  threads: 'bg-gray-900',
}

// ── sub-components ────────────────────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = () => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }
  return (
    <button
      onClick={handleCopy}
      className="ml-2 p-1 rounded hover:bg-muted transition-colors flex-shrink-0"
      title="Copy"
    >
      {copied ? (
        <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
      ) : (
        <Copy className="h-3.5 w-3.5 text-muted-foreground" />
      )}
    </button>
  )
}

function SetupGuideCard({ platform }: { platform: PlatformSetup }) {
  const [open, setOpen] = useState(false)
  const Icon = platform.icon

  return (
    <Card className="overflow-hidden">
      <button
        className="w-full text-left"
        onClick={() => setOpen(o => !o)}
      >
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-lg ${platform.color.startsWith('bg-gradient') ? '' : platform.color}`}
                style={platform.color.startsWith('bg-gradient') ? { background: 'linear-gradient(135deg,#9333ea,#ec4899)' } : undefined}
              >
                <Icon className="h-5 w-5 text-white" />
              </div>
              <div>
                <CardTitle className="text-base">{platform.name}</CardTitle>
                <CardDescription className="text-xs mt-0.5">{platform.description}</CardDescription>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="hidden sm:flex gap-1 flex-wrap justify-end max-w-xs">
                {platform.envVars.map(v => (
                  <code key={v} className="text-[10px] px-1.5 py-0.5 bg-muted rounded font-mono">{v}</code>
                ))}
              </div>
              {open ? (
                <ChevronDown className="h-4 w-4 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              )}
            </div>
          </div>
        </CardHeader>
      </button>

      {open && (
        <CardContent className="pt-0 pb-5">
          <Separator className="mb-4" />
          <ol className="space-y-4">
            {platform.steps.map((step, i) => (
              <li key={i} className="flex gap-3">
                <span className="flex-shrink-0 w-6 h-6 rounded-full bg-primary/10 text-primary text-xs font-semibold flex items-center justify-center mt-0.5">
                  {i + 1}
                </span>
                <div className="flex-1 space-y-1.5">
                  <p className="text-sm leading-relaxed">{step.text}</p>
                  {step.code && (
                    <div className="flex items-center bg-muted rounded px-3 py-1.5">
                      <code className="text-xs font-mono flex-1 break-all">{step.code}</code>
                      <CopyButton text={step.code} />
                    </div>
                  )}
                  {step.note && (
                    <p className="text-xs text-muted-foreground italic border-l-2 border-primary/30 pl-2">{step.note}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>

          <div className="mt-5 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" asChild>
              <a href={platform.devPortalUrl} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                Open {platform.name} Developer Portal
              </a>
            </Button>
            <Button size="sm" variant="outline" asChild>
              <a href="http://localhost:8080" target="_blank" rel="noopener noreferrer">
                <Settings2 className="mr-1.5 h-3.5 w-3.5" />
                Open Env Manager to save credentials
              </a>
            </Button>
          </div>
        </CardContent>
      )}
    </Card>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────

export default function AccountsPage() {
  const [connectingPlatform, setConnectingPlatform] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'connections' | 'setup'>('connections')
  const { data: accounts, isLoading } = useAccounts()
  const connectMutation = useConnectAccount()
  const disconnectMutation = useDisconnectAccount()

  const connectedAccounts = accounts || []
  const { data: scheduledPosts = [] } = useScheduledPosts()

  const upcomingForPlatform = (platformId: string): Post[] => {
    const now = new Date()
    return (scheduledPosts as Post[])
      .filter(p =>
        p.scheduled_at &&
        new Date(p.scheduled_at) > now &&
        p.targets?.some((t: PostTarget) =>
          t.platform === platformId ||
          (t.social_account as SocialAccount | undefined)?.platform === platformId
        )
      )
      .sort((a, b) => new Date(a.scheduled_at!).getTime() - new Date(b.scheduled_at!).getTime())
      .slice(0, 3)
  }

  const handleConnect = async (platformId: string) => {
    setConnectingPlatform(platformId)
    try {
      const result = await connectMutation.mutateAsync({ platform: platformId, teamId: 'default' })
      const authUrl = (result as { data?: { authorization_url?: string } })?.data?.authorization_url
      if (authUrl) {
        window.location.href = authUrl
      } else {
        toast.success(`${platforms.find(p => p.id === platformId)?.name} connected!`)
      }
    } catch {
      toast.error('Failed to connect — make sure credentials are saved in the Env Manager')
    } finally {
      setConnectingPlatform(null)
    }
  }

  const handleDisconnect = async (accountId: string, platformName: string) => {
    if (confirm(`Disconnect ${platformName}?`)) {
      try {
        await disconnectMutation.mutateAsync(accountId)
        toast.success(`${platformName} disconnected`)
      } catch {
        toast.error('Failed to disconnect')
      }
    }
  }

  const EXPIRY_WARN_DAYS = 7

  const getAccountStatus = (platformId: string) => {
    const account = connectedAccounts.find((a: SocialAccount) => a.platform === platformId)
    if (!account) return { connected: false as const, expired: false, expiringSoon: false, daysLeft: null as number | null, account: null }
    const now = new Date()
    if (account.token_expires_at) {
      const exp = new Date(account.token_expires_at)
      if (exp < now) return { connected: true as const, expired: true, expiringSoon: false, daysLeft: 0, account }
      const daysLeft = Math.ceil((exp.getTime() - now.getTime()) / 86400000)
      if (daysLeft <= EXPIRY_WARN_DAYS) return { connected: true as const, expired: false, expiringSoon: true, daysLeft, account }
      return { connected: true as const, expired: false, expiringSoon: false, daysLeft, account }
    }
    return { connected: true as const, expired: false, expiringSoon: false, daysLeft: null, account }
  }

  const healthStats = platforms.map(p => ({ platform: p, status: getAccountStatus(p.id) }))
  const expiredCount = healthStats.filter(h => h.status.expired).length
  const expiringSoonCount = healthStats.filter(h => h.status.expiringSoon).length
  const healthyCount = healthStats.filter(h => h.status.connected && !h.status.expired && !h.status.expiringSoon).length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Connected Accounts</h1>
          <p className="text-muted-foreground mt-1">Manage social media connections and configure credentials</p>
        </div>
        <Button variant="outline" asChild>
          <a href="http://localhost:8080" target="_blank" rel="noopener noreferrer">
            <Settings2 className="mr-2 h-4 w-4" />
            Env Manager
          </a>
        </Button>
      </div>

      {/* Connection health banner */}
      {activeTab === 'connections' && connectedAccounts.length > 0 && (
        <div className={`rounded-xl border p-4 ${
          expiredCount > 0 ? 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/30'
          : expiringSoonCount > 0 ? 'border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30'
          : 'border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/30'
        }`}>
          <div className="flex items-center gap-3 flex-wrap">
            {expiredCount > 0
              ? <ShieldX className="h-5 w-5 text-red-600 dark:text-red-400 flex-shrink-0" />
              : expiringSoonCount > 0
                ? <ShieldAlert className="h-5 w-5 text-amber-600 dark:text-amber-400 flex-shrink-0" />
                : <ShieldCheck className="h-5 w-5 text-green-600 dark:text-green-400 flex-shrink-0" />
            }
            <div className="flex-1 min-w-0">
              <p className={`font-semibold text-sm ${
                expiredCount > 0 ? 'text-red-800 dark:text-red-300'
                : expiringSoonCount > 0 ? 'text-amber-800 dark:text-amber-300'
                : 'text-green-800 dark:text-green-300'
              }`}>
                {expiredCount > 0
                  ? `${expiredCount} token${expiredCount > 1 ? 's' : ''} expired — reconnect required`
                  : expiringSoonCount > 0
                    ? `${expiringSoonCount} token${expiringSoonCount > 1 ? 's' : ''} expiring within ${EXPIRY_WARN_DAYS} days`
                    : `All ${healthyCount} connection${healthyCount !== 1 ? 's' : ''} healthy`
                }
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {healthyCount} healthy · {expiringSoonCount} expiring soon · {expiredCount} expired
              </p>
            </div>
            {/* Platform health dots */}
            <div className="flex items-center gap-2">
              {healthStats.map(({ platform, status }) => {
                const Icon = platform.icon
                return (
                  <div key={platform.id} className="relative" title={`${platform.name}: ${status.expired ? 'expired' : status.expiringSoon ? `${status.daysLeft}d left` : status.connected ? 'active' : 'not connected'}`}>
                    <div className={`p-1.5 rounded-full ${platform.color.startsWith('bg-gradient') ? '' : platform.color} ${!status.connected ? 'opacity-20' : ''}`}
                      style={platform.color.startsWith('bg-gradient') ? { background: 'linear-gradient(135deg,#9333ea,#ec4899)', opacity: status.connected ? 1 : 0.2 } : undefined}
                    >
                      <Icon className="h-3 w-3 text-white" />
                    </div>
                    {status.expired && (
                      <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-red-500 border border-background" />
                    )}
                    {status.expiringSoon && !status.expired && (
                      <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-amber-400 border border-background" />
                    )}
                    {status.connected && !status.expired && !status.expiringSoon && (
                      <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-green-500 border border-background" />
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {/* Tab toggle */}
      <div className="flex gap-2 border-b">
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === 'connections' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          onClick={() => setActiveTab('connections')}
        >
          Connections
        </button>
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5 ${activeTab === 'setup' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          onClick={() => setActiveTab('setup')}
        >
          <BookOpen className="h-3.5 w-3.5" />
          Credential Setup Guide
        </button>
      </div>

      {/* ── CONNECTIONS TAB ── */}
      {activeTab === 'connections' && (
        <>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {platforms.map((platform) => {
              const Icon = platform.icon
              const status = getAccountStatus(platform.id)
              const isConnecting = connectingPlatform === platform.id

              return (
                <Card key={platform.id} className="relative overflow-hidden">
                  <div
                    className={`absolute top-0 left-0 h-full w-1 ${platform.color.startsWith('bg-gradient') ? '' : platform.color}`}
                    style={platform.color.startsWith('bg-gradient') ? { background: 'linear-gradient(to bottom,#9333ea,#ec4899)' } : undefined}
                  />
                  <CardHeader>
                    <div className="flex items-center gap-3">
                      <div
                        className={`p-3 rounded-xl ${platform.color.startsWith('bg-gradient') ? '' : platform.color}`}
                        style={platform.color.startsWith('bg-gradient') ? { background: 'linear-gradient(135deg,#9333ea,#ec4899)' } : undefined}
                      >
                        <Icon className="h-6 w-6 text-white" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <CardTitle className="text-lg truncate">{platform.name}</CardTitle>
                        <CardDescription className="text-xs">{platform.description}</CardDescription>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="pt-0">
                    {status.connected ? (
                      <>
                        {status.expired && (
                          <div className="mb-3 flex items-center gap-1.5 text-xs text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-950/30 rounded px-2 py-1.5">
                            <ShieldX className="h-3 w-3 flex-shrink-0" />
                            Token expired — reconnect to continue posting
                          </div>
                        )}
                        {status.expiringSoon && !status.expired && (
                          <div className="mb-3 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/30 rounded px-2 py-1.5">
                            <Clock className="h-3 w-3 flex-shrink-0" />
                            Token expires in {status.daysLeft} day{status.daysLeft !== 1 ? 's' : ''} — reconnect soon
                          </div>
                        )}
                        <div className="space-y-2 text-sm">
                          <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Account</span>
                            <span className="font-medium truncate max-w-[150px]">
                              {status.account?.username || status.account?.display_name || 'Connected'}
                            </span>
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Status</span>
                            <Badge variant={status.expired ? 'destructive' : status.expiringSoon ? 'outline' : 'success'}>
                              {status.expired ? 'Expired' : status.expiringSoon ? `${status.daysLeft}d left` : 'Active'}
                            </Badge>
                          </div>
                        </div>
                        <div className="mt-4 flex gap-2">
                          {(status.expired || status.expiringSoon) && (
                            <Button
                              size="sm"
                              className="flex-1"
                              onClick={() => handleConnect(platform.id)}
                              disabled={connectingPlatform === platform.id}
                            >
                              {connectingPlatform === platform.id
                                ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                              }
                              Reconnect
                            </Button>
                          )}
                          <Button
                            variant="outline"
                            size="sm"
                            className="flex-1"
                            onClick={() => handleDisconnect(status.account!.id, platform.name)}
                            disabled={disconnectMutation.isPending}
                          >
                            <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                            Disconnect
                          </Button>
                        </div>

                        {/* Queue peek — upcoming scheduled posts for this platform */}
                        {(() => {
                          const upcoming = upcomingForPlatform(platform.id)
                          if (upcoming.length === 0) return null
                          return (
                            <div className="mt-4 border-t pt-3 space-y-1.5">
                              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1">
                                <Clock className="h-3 w-3" />
                                Next up ({upcoming.length})
                              </p>
                              {upcoming.map(post => (
                                <Link
                                  key={post.id}
                                  href={`/content/${post.id}/edit`}
                                  className="flex items-center gap-2 rounded px-2 py-1.5 hover:bg-muted/60 transition-colors group"
                                >
                                  <span className="text-[10px] tabular-nums text-muted-foreground w-[52px] flex-shrink-0">
                                    {post.scheduled_at
                                      ? new Date(post.scheduled_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                                      : '—'}
                                  </span>
                                  <span className="text-xs text-foreground truncate flex-1">
                                    {post.content_text?.slice(0, 36) ?? '—'}{(post.content_text?.length ?? 0) > 36 ? '…' : ''}
                                  </span>
                                </Link>
                              ))}
                              <Link href="/calendar" className="block text-[10px] text-muted-foreground hover:text-primary transition-colors pt-1 pl-1">
                                View full calendar →
                              </Link>
                            </div>
                          )
                        })()}
                      </>
                    ) : (
                      <div className="text-center py-4">
                        <p className="text-sm text-muted-foreground mb-1">Not connected</p>
                        <p className="text-xs text-amber-600 mb-3">
                          Configure credentials first →{' '}
                          <button className="underline" onClick={() => setActiveTab('setup')}>Setup Guide</button>
                        </p>
                        <Button
                          className="w-full"
                          onClick={() => handleConnect(platform.id)}
                          disabled={isConnecting || connectMutation.isPending}
                        >
                          {isConnecting ? (
                            <>
                              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                              Connecting…
                            </>
                          ) : (
                            <>
                              <Icon className="mr-2 h-4 w-4" />
                              Connect {platform.name}
                            </>
                          )}
                        </Button>
                      </div>
                    )}
                  </CardContent>
                </Card>
              )
            })}
          </div>

          {/* Connected accounts detail list */}
          {connectedAccounts.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Account Details</CardTitle>
                <CardDescription>All connected accounts and their permissions</CardDescription>
              </CardHeader>
              <CardContent>
                {isLoading ? (
                  <div className="space-y-3">
                    {[1, 2].map(i => <Skeleton key={i} className="h-14 w-full" />)}
                  </div>
                ) : (
                  <div className="space-y-3">
                    {connectedAccounts.map((account: SocialAccount) => {
                      const platform = platforms.find(p => p.id === account.platform)
                      const Icon = platform?.icon || AlertCircle
                      const color = platformColors[account.platform] || 'bg-gray-500'
                      const isExpired = account.token_expires_at && new Date(account.token_expires_at) < new Date()

                      return (
                        <div key={account.id} className="flex items-center justify-between p-4 rounded-lg border">
                          <div className="flex items-center gap-4">
                            <div className={`p-2 rounded-lg ${color}`}>
                              <Icon className="h-5 w-5 text-white" />
                            </div>
                            <div>
                              <p className="font-medium">{platform?.name || account.platform}</p>
                              <p className="text-sm text-muted-foreground">@{account.username || account.display_name || 'Unknown'}</p>
                            </div>
                          </div>
                          <div className="flex items-center gap-3">
                            <Badge variant={isExpired ? 'destructive' : 'success'}>
                              {isExpired ? 'Token Expired' : 'Active'}
                            </Badge>
                            <Button variant="ghost" size="icon" onClick={() => handleDisconnect(account.id, platform?.name || account.platform)}>
                              <Trash2 className="h-4 w-4 text-destructive" />
                            </Button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Quick link to setup if no accounts */}
          {!isLoading && connectedAccounts.length === 0 && (
            <Card className="border-dashed">
              <CardContent className="py-10 text-center">
                <CheckCircle2 className="h-10 w-10 text-muted-foreground/30 mx-auto mb-3" />
                <p className="font-medium mb-1">No accounts connected yet</p>
                <p className="text-sm text-muted-foreground mb-4">
                  Follow the credential setup guide, save your keys in the Env Manager, then connect here.
                </p>
                <Button variant="outline" onClick={() => setActiveTab('setup')}>
                  <BookOpen className="mr-2 h-4 w-4" />
                  Open Setup Guide
                </Button>
              </CardContent>
            </Card>
          )}
        </>
      )}

      {/* ── SETUP GUIDE TAB ── */}
      {activeTab === 'setup' && (
        <div className="space-y-4">
          {/* How it works banner */}
          <Card className="bg-primary/5 border-primary/20">
            <CardContent className="py-4">
              <div className="flex gap-4">
                <div className="flex-shrink-0 space-y-2 text-sm">
                  <p className="font-semibold text-primary">How credential setup works</p>
                  <ol className="space-y-1 text-muted-foreground list-decimal list-inside">
                    <li>Create a developer app on the platform's portal (see guide per platform below)</li>
                    <li>Copy the Client ID and Client Secret from the portal</li>
                    <li>
                      Open the{' '}
                      <a href="http://localhost:8080" target="_blank" rel="noopener noreferrer" className="text-primary underline font-medium">
                        Env Manager ↗
                      </a>
                      {' '}(login: <code className="font-mono text-xs bg-muted px-1 rounded">admin / admin</code>) and paste the values
                    </li>
                    <li>Click Save in the Env Manager — the backend picks up new vars automatically</li>
                    <li>Return to "Connections" and click Connect</li>
                  </ol>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Per-platform guides */}
          <div className="space-y-3">
            {platforms.map(platform => (
              <SetupGuideCard key={platform.id} platform={platform} />
            ))}
          </div>

          {/* Env Manager shortcut */}
          <Card>
            <CardContent className="py-5">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div>
                  <p className="font-medium">Ready to save your credentials?</p>
                  <p className="text-sm text-muted-foreground mt-0.5">
                    The Env Manager lets you set all environment variables without editing files.
                    Login with <code className="font-mono text-xs bg-muted px-1 rounded">admin / admin</code>.
                  </p>
                </div>
                <Button asChild className="flex-shrink-0">
                  <a href="http://localhost:8080" target="_blank" rel="noopener noreferrer">
                    <Settings2 className="mr-2 h-4 w-4" />
                    Open Env Manager
                  </a>
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
