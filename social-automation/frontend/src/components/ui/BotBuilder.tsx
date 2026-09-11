'use client'

import { useState, useEffect } from 'react'
import { Bot, Plus, Power, PowerOff, Save, Loader2, Clock, MessageSquare, Sparkles, Play } from 'lucide-react'
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
  })

  // Fetch personalities
  const personalitiesQuery = useQuery({
    queryKey: ['bot-personalities', accountId],
    queryFn: async () => {
      const resp = await messengerApi.getBotPersonalities(accountId)
      return resp.data
    },
    enabled: !!accountId,
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

  if (isLoading) {
    return (
      <Card>
        <CardContent className="p-6 flex items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
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
              Create an AI-powered auto-reply bot for your Messenger. The bot uses DMR (local Qwen3 8B) first,
              with Cloudflare Workers AI as fallback. It supports brand knowledge RAG, conversation memory,
              intent detection, per-conversation cooldowns, and human handoff.
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
        <BotConfigEditor accountId={accountId} bot={bot} accountType={accountType} />
      </CardContent>
    </Card>
  )
}

// ── Create Bot Form ────────────────────────────────────────────────

function CreateBotForm({
  accountId,
  personalities,
  onSuccess,
}: {
  accountId: string
  personalities: Personality[]
  onSuccess: () => void
}) {
  const [name, setName] = useState('Cloudless Assistant')
  const [personality, setPersonality] = useState('professional_friendly')
  const [language, setLanguage] = useState('auto')
  const [businessHoursStart, setBusinessHoursStart] = useState('')
  const [businessHoursEnd, setBusinessHoursEnd] = useState('')
  const [customPrompt, setCustomPrompt] = useState('')

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
            disabled={createMutation.isPending || !name}
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
}: {
  accountId: string
  bot: BotConfig
  accountType: 'page' | 'user'
}) {
  const queryClient = useQueryClient()
  const [config, setConfig] = useState<BotConfig>(bot)

  useEffect(() => {
    setConfig(bot)
  }, [bot])

  const updateMutation = useMutation({
    mutationFn: (data: Partial<BotConfig>) => messengerApi.updateBot(accountId, data),
    onSuccess: () => {
      toast.success('Bot settings saved')
      queryClient.invalidateQueries({ queryKey: ['messenger-bot', accountId] })
    },
    onError: () => toast.error('Failed to save bot settings'),
  })

  const handleSave = () => {
    updateMutation.mutate(config)
  }

  return (
    <div className="space-y-4">
      {/* Account type banner */}
      <div className="text-xs text-muted-foreground bg-accent/50 rounded-lg p-2">
        {accountType === 'page'
          ? 'Business Page bot — replies via Messenger Platform API (Graph API)'
          : 'Personal account bot — replies via browser bridge (CDP + noVNC)'}
      </div>

      {/* System prompt */}
      <div>
        <label className="text-sm font-medium block mb-1 flex items-center gap-1">
          <MessageSquare className="h-3 w-3" />
          System Prompt
        </label>
        <Textarea
          value={config.system_prompt}
          onChange={(e) => setConfig({ ...config, system_prompt: e.target.value })}
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
            onChange={(e) => setConfig({ ...config, model: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
          >
            <option value="ai/qwen3:8b-q4_K_M">Qwen3 8B (DMR, local — recommended)</option>
            <option value="ai/llama3.2:latest">Llama 3.2 (DMR, local)</option>
            <option value="ai/gemma3:latest">Gemma 3 (DMR, local)</option>
            <option value="ai/phi4:latest">Phi-4 (DMR, local)</option>
            <option value="ai/qwen2.5:latest">Qwen 2.5 (DMR, local)</option>
            <option value="@cf/meta/llama-3.1-8b-instruct">Llama 3.1 8B (Cloudflare, fallback)</option>
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
            onChange={(e) => setConfig({ ...config, temperature: parseFloat(e.target.value) })}
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
            onChange={(e) => setConfig({ ...config, max_tokens: parseInt(e.target.value) || 300 })}
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
            onChange={(e) => setConfig({ ...config, cooldown_seconds: parseInt(e.target.value) || 300 })}
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
            onChange={(e) => setConfig({ ...config, business_hours_start: e.target.value || null })}
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
            onChange={(e) => setConfig({ ...config, business_hours_end: e.target.value || null })}
            className="w-full px-3 py-2 rounded-lg border bg-card text-sm"
          />
        </div>
      </div>

      {/* Fallback text */}
      <div>
        <label className="text-sm font-medium block mb-1">Fallback Text</label>
        <input
          type="text"
          value={config.fallback_text}
          onChange={(e) => setConfig({ ...config, fallback_text: e.target.value })}
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
            onChange={(e) => setConfig({ ...config, reply_to_greetings: e.target.checked })}
          />
        </div>
        <div className="flex items-center justify-between p-2 rounded-lg border bg-card">
          <span className="text-sm">Reply to spam</span>
          <input
            type="checkbox"
            checked={config.reply_to_spam}
            onChange={(e) => setConfig({ ...config, reply_to_spam: e.target.checked })}
          />
        </div>
      </div>

      {/* Paused threads (human handoff) */}
      {config.paused_threads.length > 0 && (
        <div>
          <label className="text-sm font-medium block mb-1">Paused Threads (Human Handoff)</label>
          <div className="space-y-1">
            {config.paused_threads.map((threadId) => (
              <div key={threadId} className="flex items-center justify-between p-2 rounded-lg border bg-card">
                <span className="text-xs font-mono">{threadId}</span>
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
            ))}
          </div>
        </div>
      )}

      {/* Save button */}
      <Button onClick={handleSave} disabled={updateMutation.isPending} size="sm">
        {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Save Bot Settings
      </Button>
    </div>
  )
}
