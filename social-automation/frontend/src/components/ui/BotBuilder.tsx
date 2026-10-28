'use client'

import { useState, useEffect, useRef } from 'react'
import { Bot, Plus, Power, PowerOff, Save, Loader2, Clock, MessageSquare, Sparkles, Play, AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Textarea } from '@/components/ui/Textarea'
import { messengerApi } from '@/services/api'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'

interface BotConfig {
  name: string
  enabled: boolean
  system_prompt: string
  model: string
  fallback_text: string
  max_tokens: number
  temperature: number
  cooldown_seconds: number
  business_hours_start: string | null
  business_hours_end: string | null
  paused_threads: string[]
  reply_to_spam: boolean
  reply_to_greetings: boolean
  quick_replies: { title: string; payload: string }[]
}

interface Personality {
  id: string
  name: string
  description: string
}

interface BotBuilderProps {
  accountId: string
  accountType: 'page' | 'user'
}

export function BotBuilder({ accountId, accountType }: BotBuilderProps) {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)

  // Fetch bot status
  const botQuery = useQuery({
    queryKey: ['messenger-bot', accountId],
    queryFn: async () => {
      const resp = await messengerApi.getBot(accountId)
      return resp.data
    },
    enabled: !!accountId,
    retry: 1,
  })

  // Fetch personalities
  const personalitiesQuery = useQuery({
    queryKey: ['bot-personalities', accountId],
    queryFn: async () => {
      const resp = await messengerApi.getBotPersonalities(accountId)
      return resp.data
    },
    enabled: !!accountId,
    staleTime: 5 * 60 * 1000, // personalities rarely change
  })

  const activateMutation = useMutation({
    mutationFn: () => messengerApi.activateBot(accountId),
    onSuccess: () => {
      toast.success('Bot activated — auto-replies are live')
      queryClient.invalidateQueries({ queryKey: ['messenger-bot', accountId] })
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : 'Failed to activate bot')
    },
  })

  const deactivateMutation = useMutation({
    mutationFn: () => messengerApi.deactivateBot(accountId),
    onSuccess: () => {
      toast.success('Bot deactivated — auto-replies paused')
      queryClient.invalidateQueries({ queryKey: ['messenger-bot', accountId] })
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : 'Failed to deactivate bot')
    },
  })

  const bot = botQuery.data?.bot as BotConfig | undefined
  const exists = botQuery.data?.exists === true
  const isLoading = botQuery.isLoading
  const isError = botQuery.isError

  if (isLoading) {
    return (
      <Card>
        <CardContent className="p-6 flex items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    )
  }

  // Error state — don't confuse errors with "no bot exists"
  if (isError && !botQuery.data) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Bot className="h-4 w-4" />
            Bot Builder
            <span className="text-xs text-muted-foreground font-normal">
              ({accountType === 'page' ? 'Business Page' : 'Personal Account'})
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-start gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 text-sm">
            <AlertCircle className="h-4 w-4 text-red-500 mt-0.5 shrink-0" />
            <div className="space-y-2">
              <p className="font-medium text-red-700 dark:text-red-300">Failed to load bot</p>
              <p className="text-red-600 dark:text-red-400 text-xs">
                {(botQuery.error as Error)?.message || 'Could not reach the API. Check that social-api is running.'}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => botQuery.refetch()}
                disabled={botQuery.isFetching}
              >
                {botQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Retry
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    )
  }

  // No bot yet — show create form
  if (!exists) {
    return (
      <>
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Bot className="h-4 w-4" />
              Bot Builder
              <span className="text-xs text-muted-foreground font-normal">
                ({accountType === 'page' ? 'Business Page' : 'Personal Account'})
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Create an AI-powered auto-reply bot for your Messenger. The bot uses DMR (local Llama 3.2) first
              for English, with Cloudflare Workers AI for Greek and as fallback. It supports brand knowledge RAG,
              conversation memory, intent detection, per-conversation cooldowns, and human handoff.
            </p>
            <Button onClick={() => setShowCreate(!showCreate)} size="sm">
              <Plus className="h-4 w-4" />
              Create Bot
            </Button>
          </CardContent>
        </Card>

        {showCreate && (
          <CreateBotForm
            accountId={accountId}
            personalities={personalitiesQuery.data?.personalities || []}
            personalitiesLoading={personalitiesQuery.isLoading}
            onSuccess={() => {
              setShowCreate(false)
              queryClient.invalidateQueries({ queryKey: ['messenger-bot', accountId] })
            }}
          />
        )}
      </>
    )
  }

  // Bot exists — show config + controls
  if (!bot) return null
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Bot className="h-4 w-4" />
            Bot: {bot.name}
            <span className={`text-xs px-2 py-0.5 rounded-full ${
              bot.enabled
                ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'
                : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'
            }`}>
              {bot.enabled ? '● Active' : '○ Inactive'}
            </span>
          </span>
          <div className="flex gap-2">
            {bot.enabled ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => deactivateMutation.mutate()}
                disabled={deactivateMutation.isPending}
              >
                {deactivateMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <PowerOff className="h-4 w-4" />
                )}
                Deactivate
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={() => activateMutation.mutate()}
                disabled={activateMutation.isPending}
              >
                {activateMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Power className="h-4 w-4" />
                )}
                Activate
              </Button>
            )}
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <BotConfigEditor
          accountId={accountId}
          bot={bot}
          accountType={accountType}
          pausedThreadsStatus={botQuery.data?.paused_threads_status}
        />
      </CardContent>
    </Card>
  )
}

// ── Create Bot Form ────────────────────────────────────────────────

function CreateBotForm({
  accountId,
  personalities,
  personalitiesLoading,
  onSuccess,
}: {
  accountId: string
  personalities: Personality[]
  personalitiesLoading: boolean
  onSuccess: () => void
}) {
  const [name, setName] = useState('Cloudless Assistant')
  const [personality, setPersonality] = useState('professional_friendly')
  const [language, setLanguage] = useState('auto')
  const [businessHoursStart, setBusinessHoursStart] = useState('')
  const [businessHoursEnd, setBusinessHoursEnd] = useState('')
  const [customPrompt, setCustomPrompt] = useState('')

  const businessHoursValid = !businessHoursStart || !businessHoursEnd || businessHoursStart < businessHoursEnd

  const createMutation = useMutation({
    mutationFn: () =>
      messengerApi.createBot(accountId, {
        name,
        personality,
        language,
        business_hours_start: businessHoursStart || undefined,
        business_hours_end: businessHoursEnd || undefined,
        custom_prompt: customPrompt || undefined,
      }),
    onSuccess: (resp) => {
      const data = resp.data as { brand_indexed?: number; account_type?: string }
      toast.success(
        `Bot created! ${data.brand_indexed ? `Brand knowledge indexed (${data.brand_indexed} docs).` : ''} Activate it to start auto-replying.`
      )
      onSuccess()
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : 'Failed to create bot')
    },
  })

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Sparkles className="h-4 w-4" />
          Create New Bot
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Bot name */}
        <div>
          <label className="text-sm font-medium block mb-1">Bot Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
            placeholder="e.g. Cloudless Assistant"
          />
        </div>

        {/* Personality preset */}
        <div>
          <label className="text-sm font-medium block mb-1">Personality</label>
          {personalitiesLoading ? (
            <div className="flex items-center justify-center p-4 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              Loading personalities...
            </div>
          ) : personalities.length === 0 ? (
            <div className="p-3 rounded-lg border bg-accent/50 text-sm text-muted-foreground">
              No personality presets available. A default will be used.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {personalities.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setPersonality(p.id)}
                  className={`text-left p-3 rounded-lg border text-sm transition-colors ${
                    personality === p.id
                      ? 'bg-primary text-primary-foreground border-primary'
                      : 'bg-card hover:bg-accent border-border'
                  }`}
                >
                  <div className="font-medium">{p.name}</div>
                  <div className={`text-xs mt-1 ${personality === p.id ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}>
                    {p.description}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Language */}
        <div>
          <label className="text-sm font-medium block mb-1">Language</label>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
          >
            <option value="auto">Auto (match incoming message)</option>
            <option value="en">English</option>
            <option value="el">Greek (Ελληνικά)</option>
          </select>
        </div>

        {/* Business hours (optional) */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-sm font-medium block mb-1 flex items-center gap-1">
              <Clock className="h-3 w-3" />
              Business Hours Start (UTC, optional)
            </label>
            <input
              type="time"
              value={businessHoursStart}
              onChange={(e) => setBusinessHoursStart(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
            />
          </div>
          <div>
            <label className="text-sm font-medium block mb-1 flex items-center gap-1">
              <Clock className="h-3 w-3" />
              Business Hours End (UTC, optional)
            </label>
            <input
              type="time"
              value={businessHoursEnd}
              onChange={(e) => setBusinessHoursEnd(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
            />
          </div>
        </div>
        {!businessHoursValid && (
          <p className="text-xs text-red-500">
            End time must be after start time.
          </p>
        )}

        {/* Custom prompt (optional) */}
        <div>
          <label className="text-sm font-medium block mb-1">
            Custom System Prompt (optional — overrides personality preset)
          </label>
          <Textarea
            value={customPrompt}
            onChange={(e) => setCustomPrompt(e.target.value)}
            rows={3}
            placeholder="Leave empty to use the personality preset. Use {page_name} as a placeholder."
            className="text-sm"
          />
        </div>

        {/* Submit */}
        <div className="flex gap-2">
          <Button
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending || !name || !businessHoursValid}
            size="sm"
          >
            {createMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            Create Bot
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

// ── Bot Config Editor ───────────────────────────────────────────────

function BotConfigEditor({
  accountId,
  bot,
  accountType,
  pausedThreadsStatus,
}: {
  accountId: string
  bot: BotConfig
  accountType: 'page' | 'user'
  pausedThreadsStatus?: Record<string, boolean>
}) {
  const queryClient = useQueryClient()
  const [config, setConfig] = useState<BotConfig>(bot)
  // Track whether the user has unsaved edits — prevents refetch from overwriting
  const [isDirty, setIsDirty] = useState(false)
  const lastBotRef = useRef(bot)

  useEffect(() => {
    // Only sync from server if the bot reference actually changed AND user has no unsaved edits
    // This prevents activate/deactivate refetches from wiping in-progress edits
    if (lastBotRef.current !== bot && !isDirty) {
      setConfig(bot)
      lastBotRef.current = bot
    }
  }, [bot, isDirty])

  const updateConfig = (patch: Partial<BotConfig>) => {
    setConfig((prev) => ({ ...prev, ...patch }))
    setIsDirty(true)
  }

  const updateMutation = useMutation({
    mutationFn: (data: Partial<BotConfig>) => messengerApi.updateBot(accountId, data),
    onSuccess: () => {
      toast.success('Bot settings saved')
      setIsDirty(false)
      queryClient.invalidateQueries({ queryKey: ['messenger-bot', accountId] })
    },
    onError: () => toast.error('Failed to save bot settings'),
  })

  const handleSave = () => {
    updateMutation.mutate(config)
  }

  const handleReset = () => {
    setConfig(bot)
    setIsDirty(false)
  }

  return (
    <div className="space-y-4">
      {/* Account type banner */}
      <div className="text-xs text-muted-foreground bg-accent/50 rounded-lg p-2">
        {accountType === 'page'
          ? 'Business Page bot — replies via Messenger Platform API (Graph API)'
          : 'Personal account bot — replies via browser bridge (CDP + noVNC)'}
      </div>

      {/* Dirty indicator */}
      {isDirty && (
        <div className="flex items-center justify-between p-2 rounded-lg bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-900 text-xs">
          <span className="text-amber-700 dark:text-amber-300">You have unsaved changes</span>
          <button onClick={handleReset} className="text-amber-700 dark:text-amber-300 underline">
            Discard
          </button>
        </div>
      )}

      {/* System prompt */}
      <div>
        <label className="text-sm font-medium block mb-1 flex items-center gap-1">
          <MessageSquare className="h-3 w-3" />
          System Prompt
        </label>
        <Textarea
          value={config.system_prompt}
          onChange={(e) => updateConfig({ system_prompt: e.target.value })}
          rows={4}
          className="text-sm"
        />
        <p className="text-xs text-muted-foreground mt-1">
          Use {`{page_name}`} as a placeholder for the account name. The bot replies in the same language as the incoming message.
        </p>
      </div>

      {/* Model + temperature */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-sm font-medium block mb-1">AI Model</label>
          <select
            value={config.model}
            onChange={(e) => updateConfig({ model: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
          >
            <option value="ai/llama3.2:latest">Llama 3.2 (DMR, local — recommended for English)</option>
            <option value="ai/qwen3:8b-q4_K_M">Qwen3 8B (DMR, local — reasoning mode)</option>
            <option value="ai/gemma3:latest">Gemma 3 (DMR, local)</option>
            <option value="ai/phi4:latest">Phi-4 (DMR, local)</option>
            <option value="ai/qwen2.5:latest">Qwen 2.5 (DMR, local)</option>
            <option value="@cf/meta/llama-3.1-8b-instruct">Llama 3.1 8B (Cloudflare — recommended for Greek)</option>
          </select>
        </div>
        <div>
          <label className="text-sm font-medium block mb-1">
            Temperature: {config.temperature.toFixed(1)}
          </label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.1"
            value={config.temperature}
            onChange={(e) => updateConfig({ temperature: parseFloat(e.target.value) })}
            className="w-full"
          />
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>Deterministic</span>
            <span>Creative</span>
          </div>
        </div>
      </div>

      {/* Max tokens + cooldown */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-sm font-medium block mb-1">Max Tokens</label>
          <input
            type="number"
            value={config.max_tokens}
            onChange={(e) => updateConfig({ max_tokens: parseInt(e.target.value) || 300 })}
            className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
            min="50"
            max="1000"
          />
        </div>
        <div>
          <label className="text-sm font-medium block mb-1">Cooldown (seconds)</label>
          <input
            type="number"
            value={config.cooldown_seconds}
            onChange={(e) => updateConfig({ cooldown_seconds: parseInt(e.target.value) || 300 })}
            className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
            min="0"
            max="3600"
          />
        </div>
      </div>

      {/* Business hours */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-sm font-medium block mb-1 flex items-center gap-1">
            <Clock className="h-3 w-3" />
            Business Hours Start (UTC)
          </label>
          <input
            type="time"
            value={config.business_hours_start || ''}
            onChange={(e) => updateConfig({ business_hours_start: e.target.value || null })}
            className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
          />
        </div>
        <div>
          <label className="text-sm font-medium block mb-1 flex items-center gap-1">
            <Clock className="h-3 w-3" />
            Business Hours End (UTC)
          </label>
          <input
            type="time"
            value={config.business_hours_end || ''}
            onChange={(e) => updateConfig({ business_hours_end: e.target.value || null })}
            className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
          />
        </div>
      </div>
      {config.business_hours_start && config.business_hours_end && config.business_hours_start >= config.business_hours_end && (
        <p className="text-xs text-red-500">End time must be after start time.</p>
      )}

      {/* Fallback text */}
      <div>
        <label className="text-sm font-medium block mb-1">Fallback Text</label>
        <input
          type="text"
          value={config.fallback_text}
          onChange={(e) => updateConfig({ fallback_text: e.target.value })}
          className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
          placeholder="Used when AI is unavailable"
        />
      </div>

      {/* Intent toggles */}
      <div className="space-y-2">
        <label className="text-sm font-medium">Intent-Based Reply Rules</label>
        <div className="flex items-center justify-between p-2 rounded-lg border bg-card">
          <span className="text-sm">Reply to greetings</span>
          <input
            type="checkbox"
            checked={config.reply_to_greetings}
            onChange={(e) => updateConfig({ reply_to_greetings: e.target.checked })}
          />
        </div>
        <div className="flex items-center justify-between p-2 rounded-lg border bg-card">
          <span className="text-sm">Reply to spam</span>
          <input
            type="checkbox"
            checked={config.reply_to_spam}
            onChange={(e) => updateConfig({ reply_to_spam: e.target.checked })}
          />
        </div>
      </div>

      {/* Quick replies (Page only — personal accounts don't support Graph API quick replies) */}
      {accountType === 'page' && config.quick_replies.length > 0 && (
        <div>
          <label className="text-sm font-medium block mb-1">Quick Replies (Page only)</label>
          <div className="space-y-1">
            {config.quick_replies.map((qr, i) => (
              <div key={i} className="flex items-center gap-2 p-2 rounded-lg border bg-card text-sm">
                <span className="font-medium">{qr.title}</span>
                <span className="text-xs text-muted-foreground font-mono">{qr.payload}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Paused threads (human handoff) */}
      {config.paused_threads.length > 0 && (
        <div>
          <label className="text-sm font-medium block mb-1">Paused Threads (Human Handoff)</label>
          <div className="space-y-1">
            {config.paused_threads.map((threadId) => {
              const isActuallyPaused = pausedThreadsStatus?.[threadId] ?? true
              return (
                <div key={threadId} className="flex items-center justify-between p-2 rounded-lg border bg-card">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono">{threadId}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                      isActuallyPaused
                        ? 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300'
                        : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'
                    }`}>
                      {isActuallyPaused ? 'paused' : 'stale'}
                    </span>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      try {
                        await messengerApi.resumeBotThread(accountId, threadId)
                        toast.success('Thread resumed')
                        queryClient.invalidateQueries({ queryKey: ['messenger-bot', accountId] })
                      } catch {
                        toast.error('Failed to resume thread')
                      }
                    }}
                  >
                    <Play className="h-3 w-3" />
                    Resume
                  </Button>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Save button */}
      <div className="flex gap-2">
        <Button onClick={handleSave} disabled={updateMutation.isPending || !isDirty} size="sm">
          {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          Save Bot Settings
        </Button>
        {isDirty && (
          <Button onClick={handleReset} variant="outline" size="sm" disabled={updateMutation.isPending}>
            Discard
          </Button>
        )}
      </div>
    </div>
  )
}
