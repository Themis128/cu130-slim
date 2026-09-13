'use client'

import { useState, useEffect } from 'react'
import {
  PhoneCall, Server, Activity, RefreshCw, Loader2, AlertCircle,
  CheckCircle2, XCircle, Send, Bot, Settings, MessageSquare,
  User, Phone, Globe, Zap,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Switch } from '@/components/ui/Switch'
import { whatsappApi, accountsApi } from '@/services/api'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'

export default function WhatsAppPage() {
  const [selectedAccountId, setSelectedAccountId] = useState<string>('')
  const queryClient = useQueryClient()

  // Fetch accounts
  const { data: accountsData, isLoading: loadingAccounts } = useQuery({
    queryKey: ['whatsapp-accounts'],
    queryFn: () => accountsApi.list(),
  })

  // Auto-select the first WhatsApp account
  useEffect(() => {
    if (!selectedAccountId && accountsData?.data) {
      const waAccount = accountsData.data.find(
        (a: { platform: string; status: string }) =>
          a.platform === 'whatsapp' && a.status === 'active'
      )
      if (waAccount) {
        setSelectedAccountId(waAccount.id)
      }
    }
  }, [accountsData, selectedAccountId])

  const whatsappAccounts = (accountsData?.data || []).filter(
    (a: { platform: string; status: string }) =>
      a.platform === 'whatsapp' && a.status === 'active'
  )

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <PhoneCall className="h-6 w-6" />
          WhatsApp Business
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Manage WhatsApp Business Cloud API — send messages, templates, AI auto-reply, webhooks, and flows
        </p>
      </div>

      {/* Account selector */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Select WhatsApp Account</CardTitle>
        </CardHeader>
        <CardContent>
          {loadingAccounts ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading accounts...
            </div>
          ) : whatsappAccounts.length === 0 ? (
            <div className="text-sm text-muted-foreground space-y-2">
              <p>No WhatsApp Business accounts connected.</p>
              <p>
                Connect a WhatsApp account in the{' '}
                <a href="/accounts" className="text-primary underline">Channels</a> page first,
                then configure credentials below.
              </p>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {whatsappAccounts.map((account: {
                id: string
                display_name?: string
                account_id?: string
                meta_data?: { display_phone_number?: string; verified_name?: string }
              }) => (
                <button
                  key={account.id}
                  onClick={() => setSelectedAccountId(account.id)}
                  className={`px-4 py-2 rounded-lg border text-sm transition-colors flex items-center gap-2 ${
                    selectedAccountId === account.id
                      ? 'bg-primary text-primary-foreground border-primary'
                      : 'bg-card hover:bg-accent border-border'
                  }`}
                >
                  <Phone className="h-3 w-3" />
                  {account.meta_data?.display_phone_number ||
                    account.display_name ||
                    account.account_id ||
                    'WhatsApp Account'}
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Main content — only show when account is selected */}
      {selectedAccountId ? (
        <>
          <SetupStatusCard accountId={selectedAccountId} />
          <BusinessProfileCard accountId={selectedAccountId} />
          <SendMessageCard accountId={selectedAccountId} />
          <AutoReplyCard accountId={selectedAccountId} />
          <BotCard accountId={selectedAccountId} />
        </>
      ) : (
        <Card>
          <CardContent className="flex items-center justify-center h-[200px] text-muted-foreground text-sm">
            Select a WhatsApp account to view settings
          </CardContent>
        </Card>
      )}
    </div>
  )
}

// ------------------------------------------------------------------
// Setup Status Card
// ------------------------------------------------------------------

function SetupStatusCard({ accountId }: { accountId: string }) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['whatsapp-setup-status', accountId],
    queryFn: () => whatsappApi.getSetupStatus(accountId),
    refetchInterval: 30000,
  })

  const status = data?.data
  const isConfigured = status?.has_credentials && status?.webhook_subscribed

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Server className="h-4 w-4" />
            Setup Status
          </span>
          <Button variant="ghost" size="sm" onClick={() => refetch()} disabled={isLoading}>
            {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Checking setup status...
          </div>
        ) : (
          <div className="space-y-2">
            <StatusRow
              label="Credentials"
              ok={status?.has_credentials}
              detail={status?.has_credentials ? 'Access token and phone number ID configured' : 'Not configured — update credentials below'}
            />
            <StatusRow
              label="Webhook Subscribed"
              ok={status?.webhook_subscribed}
              detail={status?.webhook_subscribed ? 'App subscribed to WABA webhooks' : 'Not subscribed — use the Subscribe button'}
            />
            <StatusRow
              label="Phone Registered"
              ok={status?.phone_registered}
              detail={status?.phone_registered ? 'Phone number registered for API use' : 'Phone number not registered'}
            />
            {status?.display_phone_number && (
              <div className="flex items-center gap-2 text-sm pt-2 border-t">
                <Phone className="h-4 w-4 text-muted-foreground" />
                <span className="font-medium">{status.display_phone_number}</span>
                {status.verified_name && (
                  <span className="text-muted-foreground">— {status.verified_name}</span>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function StatusRow({ label, ok, detail }: { label: string; ok?: boolean; detail?: string }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      {ok ? (
        <CheckCircle2 className="h-4 w-4 text-green-500 mt-0.5" />
      ) : (
        <XCircle className="h-4 w-4 text-red-500 mt-0.5" />
      )}
      <div>
        <span className="font-medium">{label}</span>
        {detail && <p className="text-xs text-muted-foreground mt-0.5">{detail}</p>}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------
// Credentials Card
// ------------------------------------------------------------------

function CredentialsCard({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const [accessToken, setAccessToken] = useState('')
  const [phoneNumberId, setPhoneNumberId] = useState('')
  const [wabaId, setWabaId] = useState('')
  const [displayPhoneNumber, setDisplayPhoneNumber] = useState('')
  const [subscribeWebhooks, setSubscribeWebhooks] = useState(true)

  const mutation = useMutation({
    mutationFn: (data: {
      access_token: string
      phone_number_id: string
      waba_id?: string
      display_phone_number?: string
      subscribe_webhooks?: boolean
    }) => whatsappApi.updateCredentials(accountId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp-setup-status', accountId] })
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    mutation.mutate({
      access_token: accessToken,
      phone_number_id: phoneNumberId,
      waba_id: wabaId || undefined,
      display_phone_number: displayPhoneNumber || undefined,
      subscribe_webhooks: subscribeWebhooks,
    })
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Settings className="h-4 w-4" />
          Cloud API Credentials
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="text-sm font-medium">Access Token</label>
            <Input
              type="password"
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
              placeholder="Permanent System User access token"
              className="mt-1"
            />
            <p className="text-xs text-muted-foreground mt-1">
              Generate in Meta Business Settings → System Users → Add System User →
              assign whatsapp_business_messaging, whatsapp_business_management permissions
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">Phone Number ID</label>
              <Input
                value={phoneNumberId}
                onChange={(e) => setPhoneNumberId(e.target.value)}
                placeholder="e.g. 123456789012345"
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">WABA ID (optional)</label>
              <Input
                value={wabaId}
                onChange={(e) => setWabaId(e.target.value)}
                placeholder="WhatsApp Business Account ID"
                className="mt-1"
              />
            </div>
          </div>
          <div>
            <label className="text-sm font-medium">Display Phone Number (optional)</label>
            <Input
              value={displayPhoneNumber}
              onChange={(e) => setDisplayPhoneNumber(e.target.value)}
              placeholder="+30XXXXXXXXXX"
              className="mt-1"
            />
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={subscribeWebhooks} onCheckedChange={setSubscribeWebhooks} />
            <span className="text-sm">Subscribe to webhooks after saving</span>
          </div>
          <Button type="submit" disabled={mutation.isPending || !accessToken || !phoneNumberId}>
            {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
            Save Credentials
          </Button>
          {mutation.isError && (
            <p className="text-sm text-red-600">
              Error: {mutation.error instanceof Error ? mutation.error.message : 'Failed to save'}
            </p>
          )}
          {mutation.isSuccess && (
            <p className="text-sm text-green-600">Credentials saved successfully</p>
          )}
        </form>
      </CardContent>
    </Card>
  )
}

// ------------------------------------------------------------------
// Business Profile Card
// ------------------------------------------------------------------

function BusinessProfileCard({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['whatsapp-profile', accountId],
    queryFn: () => whatsappApi.getProfile(accountId),
  })

  const [about, setAbout] = useState('')
  const [address, setAddress] = useState('')
  const [description, setDescription] = useState('')
  const [email, setEmail] = useState('')
  const [websites, setWebsites] = useState('')

  useEffect(() => {
    if (data?.data) {
      setAbout(data.data.about || '')
      setAddress(data.data.address || '')
      setDescription(data.data.description || '')
      setEmail(data.data.email || '')
      setWebsites((data.data.websites || []).join(', '))
    }
  }, [data])

  const mutation = useMutation({
    mutationFn: (profileData: {
      about?: string
      address?: string
      description?: string
      email?: string
      websites?: string[]
    }) => whatsappApi.updateProfile(accountId, profileData),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp-profile', accountId] })
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    mutation.mutate({
      about: about || undefined,
      address: address || undefined,
      description: description || undefined,
      email: email || undefined,
      websites: websites ? websites.split(',').map((w) => w.trim()).filter(Boolean) : undefined,
    })
  }

  const profile = data?.data

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <User className="h-4 w-4" />
          Business Profile
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading profile...
          </div>
        ) : (
          <div className="space-y-4">
            {profile && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-3 rounded-lg bg-accent/50">
                <div>
                  <div className="text-xs text-muted-foreground">Verified Name</div>
                  <div className="text-sm font-medium">{profile.verified_name || 'N/A'}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">Phone</div>
                  <div className="text-sm font-medium">{profile.display_phone_number || 'N/A'}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">Quality</div>
                  <div className="text-sm font-medium">{profile.quality_rating || 'N/A'}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">Profile Picture</div>
                  <div className="text-sm font-medium">{profile.profile_picture_url ? 'Set' : 'Not set'}</div>
                </div>
              </div>
            )}
            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <label className="text-sm font-medium">About</label>
                <Textarea
                  value={about}
                  onChange={(e) => setAbout(e.target.value)}
                  placeholder="Business about text (max 139 chars)"
                  maxLength={139}
                  className="mt-1"
                  rows={2}
                />
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-sm font-medium">Address</label>
                  <Input
                    value={address}
                    onChange={(e) => setAddress(e.target.value)}
                    placeholder="Business address"
                    className="mt-1"
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">Email</label>
                  <Input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="contact@cloudless.gr"
                    className="mt-1"
                  />
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">Description</label>
                <Textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Business description"
                  className="mt-1"
                  rows={2}
                />
              </div>
              <div>
                <label className="text-sm font-medium">Websites (comma-separated)</label>
                <Input
                  value={websites}
                  onChange={(e) => setWebsites(e.target.value)}
                  placeholder="https://cloudless.gr, https://baltzakisthemis.com"
                  className="mt-1"
                />
              </div>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                Update Profile
              </Button>
              {mutation.isSuccess && (
                <p className="text-sm text-green-600 ml-2">Profile updated</p>
              )}
            </form>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ------------------------------------------------------------------
// Send Message Card
// ------------------------------------------------------------------

function SendMessageCard({ accountId }: { accountId: string }) {
  const [to, setTo] = useState('')
  const [text, setText] = useState('')
  const [imageUrl, setImageUrl] = useState('')

  const mutation = useMutation({
    mutationFn: (data: { to: string; text?: string; image_url?: string }) =>
      whatsappApi.sendMessage(accountId, data),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    mutation.mutate({
      to,
      text: text || undefined,
      image_url: imageUrl || undefined,
    })
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Send className="h-4 w-4" />
          Send Message
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="text-sm font-medium">Recipient (E.164)</label>
            <Input
              value={to}
              onChange={(e) => setTo(e.target.value)}
              placeholder="+3069XXXXXXXX"
              className="mt-1"
              required
            />
          </div>
          <div>
            <label className="text-sm font-medium">Text Message</label>
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Type your message..."
              className="mt-1"
              rows={3}
            />
          </div>
          <div>
            <label className="text-sm font-medium">Image URL (optional)</label>
            <Input
              value={imageUrl}
              onChange={(e) => setImageUrl(e.target.value)}
              placeholder="https://example.com/image.jpg"
              className="mt-1"
            />
          </div>
          <Button type="submit" disabled={mutation.isPending || !to}>
            {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Send className="h-4 w-4 mr-2" />}
            Send
          </Button>
          {mutation.isSuccess && (
            <p className="text-sm text-green-600 ml-2">Message sent successfully</p>
          )}
          {mutation.isError && (
            <p className="text-sm text-red-600 ml-2">
              Error: {mutation.error instanceof Error ? mutation.error.message : 'Failed to send'}
            </p>
          )}
        </form>
      </CardContent>
    </Card>
  )
}

// ------------------------------------------------------------------
// AI Auto-Reply Card
// ------------------------------------------------------------------

function AutoReplyCard({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['whatsapp-auto-reply', accountId],
    queryFn: () => whatsappApi.getAutoReply(accountId),
  })

  const [enabled, setEnabled] = useState(false)
  const [systemPrompt, setSystemPrompt] = useState('')
  const [model, setModel] = useState('@cf/meta/llama-3.1-8b-instruct')
  const [fallbackText, setFallbackText] = useState('')
  const [maxTokens, setMaxTokens] = useState(200)

  useEffect(() => {
    if (data?.data) {
      setEnabled(data.data.enabled || false)
      setSystemPrompt(data.data.system_prompt || '')
      setModel(data.data.model || '@cf/meta/llama-3.1-8b-instruct')
      setFallbackText(data.data.fallback_text || '')
      setMaxTokens(data.data.max_tokens || 200)
    }
  }, [data])

  const mutation = useMutation({
    mutationFn: (config: {
      enabled: boolean
      system_prompt?: string
      model?: string
      fallback_text?: string
      max_tokens?: number
    }) => whatsappApi.updateAutoReply(accountId, config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp-auto-reply', accountId] })
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    mutation.mutate({
      enabled,
      system_prompt: systemPrompt || undefined,
      model,
      fallback_text: fallbackText || undefined,
      max_tokens: maxTokens,
    })
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Bot className="h-4 w-4" />
          AI Auto-Reply
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading auto-reply config...
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="flex items-center gap-2">
              <Switch checked={enabled} onCheckedChange={setEnabled} />
              <span className="text-sm font-medium">Enable AI auto-reply</span>
            </div>
            <div>
              <label className="text-sm font-medium">System Prompt</label>
              <Textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                placeholder="You are a helpful assistant for {business_name}..."
                className="mt-1"
                rows={6}
              />
              <p className="text-xs text-muted-foreground mt-1">
                Instructions for the AI bot. Use {'{business_name}'} as a placeholder.
              </p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-sm font-medium">Model</label>
                <Input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="@cf/meta/llama-3.1-8b-instruct"
                  className="mt-1"
                />
              </div>
              <div>
                <label className="text-sm font-medium">Max Tokens</label>
                <Input
                  type="number"
                  value={maxTokens}
                  onChange={(e) => setMaxTokens(Number(e.target.value))}
                  min={50}
                  max={1000}
                  className="mt-1"
                />
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">Fallback Text</label>
              <Textarea
                value={fallbackText}
                onChange={(e) => setFallbackText(e.target.value)}
                placeholder="Thanks for your message! We'll get back to you soon."
                className="mt-1"
                rows={2}
              />
              <p className="text-xs text-muted-foreground mt-1">
                Sent when AI generation fails or the 24h window is closed.
              </p>
            </div>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
              Save Auto-Reply Config
            </Button>
            {mutation.isSuccess && (
              <p className="text-sm text-green-600 ml-2">Auto-reply config saved</p>
            )}
          </form>
        )}
      </CardContent>
    </Card>
  )
}

// ------------------------------------------------------------------
// Bot Card
// ------------------------------------------------------------------

function BotCard({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['whatsapp-bot', accountId],
    queryFn: () => whatsappApi.getBot(accountId),
  })

  const [botName, setBotName] = useState('')
  const [botEnabled, setBotEnabled] = useState(false)
  const [botPrompt, setBotPrompt] = useState('')

  useEffect(() => {
    if (data?.data) {
      setBotName(data.data.name || '')
      setBotEnabled(data.data.enabled || false)
      setBotPrompt(data.data.system_prompt || '')
    }
  }, [data])

  const updateMutation = useMutation({
    mutationFn: (botData: { name?: string; enabled?: boolean; system_prompt?: string }) =>
      whatsappApi.updateBot(accountId, botData),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp-bot', accountId] })
    },
  })

  const activateMutation = useMutation({
    mutationFn: () => whatsappApi.activateBot(accountId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp-bot', accountId] })
    },
  })

  const deactivateMutation = useMutation({
    mutationFn: () => whatsappApi.deactivateBot(accountId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp-bot', accountId] })
    },
  })

  const indexBrandMutation = useMutation({
    mutationFn: () => whatsappApi.indexBrand(accountId),
  })

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Zap className="h-4 w-4" />
          Bot Configuration
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading bot config...
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Switch checked={botEnabled} onCheckedChange={setBotEnabled} />
              <span className="text-sm font-medium">Bot enabled</span>
              {botEnabled ? (
                <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300">
                  Active
                </span>
              ) : (
                <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                  Inactive
                </span>
              )}
            </div>
            <div>
              <label className="text-sm font-medium">Bot Name</label>
              <Input
                value={botName}
                onChange={(e) => setBotName(e.target.value)}
                placeholder="Cloudless Bot"
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">System Prompt</label>
              <Textarea
                value={botPrompt}
                onChange={(e) => setBotPrompt(e.target.value)}
                placeholder="Bot system prompt..."
                className="mt-1"
                rows={6}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={() => updateMutation.mutate({ name: botName, enabled: botEnabled, system_prompt: botPrompt })}
                disabled={updateMutation.isPending}
              >
                {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Settings className="h-4 w-4 mr-1" />}
                Save Bot
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => activateMutation.mutate()}
                disabled={activateMutation.isPending || botEnabled}
              >
                <CheckCircle2 className="h-4 w-4 mr-1" />
                Activate
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => deactivateMutation.mutate()}
                disabled={deactivateMutation.isPending || !botEnabled}
              >
                <XCircle className="h-4 w-4 mr-1" />
                Deactivate
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => indexBrandMutation.mutate()}
                disabled={indexBrandMutation.isPending}
              >
                {indexBrandMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Globe className="h-4 w-4 mr-1" />}
                Index Brand
              </Button>
            </div>
            {indexBrandMutation.isSuccess && (
              <p className="text-sm text-green-600">Brand indexed for RAG-based replies</p>
            )}
            {updateMutation.isSuccess && (
              <p className="text-sm text-green-600">Bot config saved</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
