'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Linkedin, Twitter, Instagram, Facebook, Hash, CheckCircle2, AlertCircle, Loader2, RefreshCw, Trash2, ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from '@/components/ui/DropdownMenu'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/Dialog'
import { Separator } from '@/components/ui/Separator'
import { Skeleton } from '@/components/ui/Skeleton'
import { useAccounts, useConnectAccount, useDisconnectAccount } from '@/hooks/useQueries'
import type { SocialAccount } from '@/types'
import toast from 'react-hot-toast'

const platforms = [
  {
    id: 'linkedin',
    name: 'LinkedIn',
    icon: Linkedin,
    color: 'bg-blue-600',
    description: 'Professional network for B2B content',
    scopes: ['r_liteprofile', 'r_emailaddress', 'w_member_social'],
  },
  {
    id: 'twitter',
    name: 'Twitter/X',
    icon: Twitter,
    color: 'bg-sky-500',
    description: 'Real-time conversations and threads',
    scopes: ['tweet.read', 'tweet.write', 'users.read', 'offline.access'],
  },
  {
    id: 'instagram',
    name: 'Instagram',
    icon: Instagram,
    color: 'bg-pink-500',
    description: 'Visual content and Reels',
    scopes: ['user_profile', 'user_media', 'instagram_content_publish'],
  },
  {
    id: 'facebook',
    name: 'Facebook',
    icon: Facebook,
    color: 'bg-blue-700',
    description: 'Pages and Groups management',
    scopes: ['pages_read_engagement', 'pages_manage_posts', 'pages_show_list'],
  },
  {
    id: 'threads',
    name: 'Threads',
    icon: Hash,
    color: 'bg-gray-800',
    description: 'Text-based conversations from Instagram',
    scopes: ['threads_basic', 'threads_content_publish'],
  },
]

const platformIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  linkedin: Linkedin,
  twitter: Twitter,
  instagram: Instagram,
  facebook: Facebook,
  threads: Hash,
}

const platformColors: Record<string, string> = {
  linkedin: 'bg-blue-600',
  twitter: 'bg-sky-500',
  instagram: 'bg-pink-500',
  facebook: 'bg-blue-700',
  threads: 'bg-gray-800',
}

export default function AccountsPage() {
  const [connectingPlatform, setConnectingPlatform] = useState<string | null>(null)
  const { data: accounts, isLoading } = useAccounts()
  const connectMutation = useConnectAccount()
  const disconnectMutation = useDisconnectAccount()

  const connectedAccounts = accounts || []

  const handleConnect = async (platformId: string) => {
    setConnectingPlatform(platformId)
    try {
      // In a real app, this would redirect to OAuth
      // For now, we'll simulate the connection
      await connectMutation.mutateAsync({ platform: platformId, teamId: 'default' })
      toast.success(`${platforms.find(p => p.id === platformId)?.name} connected!`)
    } catch {
      toast.error('Failed to connect account')
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

  const getAccountStatus = (platformId: string) => {
    const account = connectedAccounts.find((a: SocialAccount) => a.platform === platformId)
    if (!account) return { connected: false }
    if (account.token_expires_at && new Date(account.token_expires_at) < new Date()) {
      return { connected: true, expired: true, account }
    }
    return { connected: true, account }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Connected Accounts</h1>
        <p className="text-muted-foreground mt-1">Manage your social media platform connections</p>
      </div>

      {/* Platform Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {platforms.map((platform) => {
          const Icon = platformIcons[platform.id]
          const status = getAccountStatus(platform.id)
          const isConnecting = connectingPlatform === platform.id

          return (
            <Card key={platform.id} className="relative overflow-hidden">
              <div className="absolute top-0 left-0 h-full w-1 bg-gradient-to-b" style={{ background: platform.color }} />
              <CardHeader>
                <div className="flex items-center gap-3">
                  <div className={cn('p-3 rounded-xl', platform.color)}>
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
                      <Badge className="mb-3 w-full" variant="warning" style={{ background: '#fef3c7', color: '#92400e' }}>
                        <AlertCircle className="mr-1.5 h-3 w-3" />
                        Token expired - reconnect required
                      </Badge>
                    )}
                    <div className="space-y-2 text-sm">
                      <div className="flex items-center justify-between">
                        <span className="text-muted-foreground">Account</span>
                        <span className="font-medium truncate max-w-[150px]">
                          {status.account?.account_username || status.account?.account_name || 'Connected'}
                        </span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-muted-foreground">Status</span>
                        <Badge variant={status.expired ? 'destructive' : 'success'}>
                          {status.expired ? 'Expired' : 'Active'}
                        </Badge>
                      </div>
                      {status.account?.token_expires_at && !status.expired && (
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">Expires</span>
                          <span>{new Date(status.account.token_expires_at).toLocaleDateString()}</span>
                        </div>
                      )}
                    </div>
                    <div className="mt-4 flex gap-2">
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
                      <Button
                        variant="ghost"
                        size="sm"
                        asChild
                      >
                        <a href={`/accounts/${status.account!.id}`} target="_blank" rel="noopener noreferrer">
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      </Button>
                    </div>
                  </>
                ) : (
                  <div className="text-center py-4">
                    <p className="text-sm text-muted-foreground mb-4">Not connected</p>
                    <Button
                      className="w-full"
                      onClick={() => handleConnect(platform.id)}
                      disabled={isConnecting || connectMutation.isPending}
                    >
                      {isConnecting ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Connecting...
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

      {/* Connected Accounts List */}
      {connectedAccounts.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Account Details</CardTitle>
            <CardDescription>Manage individual account settings and permissions</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {connectedAccounts.map((account: SocialAccount) => {
                const platform = platforms.find(p => p.id === account.platform)
                const Icon = platform ? platformIcons[account.platform] : AlertCircle
                const color = platform ? platformColors[account.platform] : 'bg-gray-500'
                const isExpired = account.token_expires_at && new Date(account.token_expires_at) < new Date()

                return (
                  <div
                    key={account.id}
                    className="flex items-center justify-between p-4 rounded-lg border"
                  >
                    <div className="flex items-center gap-4">
                      <div className={cn('p-2 rounded-lg', color)}>
                        <Icon className="h-5 w-5 text-white" />
                      </div>
                      <div>
                        <p className="font-medium">{platform?.name || account.platform}</p>
                        <p className="text-sm text-muted-foreground">
                          @{account.username || account.display_name || 'Unknown'}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <Badge variant={isExpired ? 'destructive' : 'success'}>
                        {isExpired ? 'Token Expired' : 'Active'}
                      </Badge>
                      {account.scopes && account.scopes.length > 0 && (
                        <Badge variant="outline" className="text-xs">
                          {account.scopes.length} permissions
                        </Badge>
                      )}
                      <Button variant="ghost" size="icon" onClick={() => handleDisconnect(account.id, platform?.name || account.platform)}>
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* OAuth Setup Guide */}
      <Card className="border-primary/20 bg-primary/5">
        <CardContent className="pt-6">
          <h3 className="font-medium text-primary mb-3">OAuth Setup Required</h3>
          <p className="text-sm text-muted-foreground mb-4">
            To connect accounts, you need to configure OAuth credentials in your environment variables:
          </p>
          <div className="grid gap-2 md:grid-cols-2 text-sm font-mono text-xs">
            {[
              'LINKEDIN_CLIENT_ID',
              'LINKEDIN_CLIENT_SECRET',
              'TWITTER_CLIENT_ID',
              'TWITTER_CLIENT_SECRET',
              'INSTAGRAM_CLIENT_ID',
              'INSTAGRAM_CLIENT_SECRET',
              'FACEBOOK_CLIENT_ID',
              'FACEBOOK_CLIENT_SECRET',
            ].map((key) => (
              <code key={key} className="px-2 py-1 bg-background rounded">{key}</code>
            ))}
          </div>
          <p className="text-xs text-muted-foreground mt-3">
            Add these to your <code className="font-mono">.env</code> file and restart the backend.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

function cn(...classes: (string | undefined | null | false)[]) {
  return classes.filter(Boolean).join(' ')
}