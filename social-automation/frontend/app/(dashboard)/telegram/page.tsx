'use client'

import { useState, useEffect } from 'react'
import {
  Send, Bot, Loader2, CheckCircle2, XCircle, RefreshCw, Settings, MessageSquare, Bell,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Switch } from '@/components/ui/Switch'
import { telegramApi, accountsApi } from '@/services/api'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { AxiosError } from 'axios'

function getErrorMessage(err: unknown, fallback = 'Unknown error'): string {
  const axiosErr = err as AxiosError<{ detail?: string }>
  return axiosErr?.response?.data?.detail || axiosErr?.message || fallback
}

export default function TelegramPage() {
  const [selectedAccountId, setSelectedAccountId] = useState('')
  const queryClient = useQueryClient()

  const { data: accountsData, isLoading: loadingAccounts } = useQuery({
    queryKey: ['telegram-accounts'],
    queryFn: () => accountsApi.list(),
  })

  useEffect(() => {
    if (!selectedAccountId && accountsData?.data) {
      const tg = accountsData.data.find(
        (a: { platform: string; status: string }) =>
          a.platform === 'telegram' && a.status === 'active'
      )
      if (tg) setSelectedAccountId(tg.id)
    }
  }, [accountsData, selectedAccountId])

  const telegramAccounts = (accountsData?.data || []).filter(
    (a: { platform: string; status: string }) =>
      a.platform === 'telegram' && a.status === 'active'
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Send className="h-6 w-6" />
          Telegram Bot
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Manage Telegram bots via the official Bot API — connect with a BotFather token,
          set webhooks, send messages, and configure AI auto-reply
        </p>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Select Telegram Bot</CardTitle>
        </CardHeader>
        <CardContent>
          {loadingAccounts ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading accounts...
            </div>
          ) : telegramAccounts.length === 0 ? (
            <div className="text-sm text-muted-foreground space-y-2">
              <p>No Telegram bots connected yet.</p>
              <p>
                Create a bot with{' '}
                <a
                  href="https://t.me/BotFather"
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary underline"
                >
                  @BotFather
                </a>
                , then paste the token below or use{' '}
                <a href="/accounts" className="text-primary underline">Channels</a>.
              </p>
              <p>
                Full setup guide: <code className="text-xs">docs/superpowers/guides/12-telegram-bot.md</code>
              </p>
              <ConnectBotCard
                onConnected={(id) => {
                  queryClient.invalidateQueries({ queryKey: ['telegram-accounts'] })
                  setSelectedAccountId(id)
                }}
              />
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {telegramAccounts.map((account: {
                id: string
                display_name?: string
                username?: string
                account_id?: string
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
                  <Send className="h-3 w-3" />
                  {account.username
                    ? `@${account.username}`
                    : account.display_name || account.account_id || 'Telegram Bot'}
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {selectedAccountId ? (
        <>
          <SetupStatusCard accountId={selectedAccountId} />
          <CredentialsCard accountId={selectedAccountId} />
          <GroupWatchCard accountId={selectedAccountId} />
          <SendMessageCard accountId={selectedAccountId} />
          <AutoReplyCard accountId={selectedAccountId} />
          <BotCard accountId={selectedAccountId} />
        </>
      ) : telegramAccounts.length > 0 ? null : null}
    </div>
  )
}

function GroupWatchCard({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['telegram-group-watch', accountId],
    queryFn: () => telegramApi.getGroupWatch(accountId),
  })
  const { data: activity } = useQuery({
    queryKey: ['telegram-group-watch-activity', accountId],
    queryFn: () => telegramApi.getGroupWatchActivity(accountId),
    refetchInterval: 60_000,
  })

  const [enabled, setEnabled] = useState(false)
  const [keywords, setKeywords] = useState('')
  const [digestEnabled, setDigestEnabled] = useState(true)
  const [digestHour, setDigestHour] = useState(9)
  const [alertMention, setAlertMention] = useState(true)
  const [alertKeywords, setAlertKeywords] = useState(true)
  const [watchAll, setWatchAll] = useState(true)

  useEffect(() => {
    const cfg = data?.data
    if (!cfg) return
    setEnabled(!!cfg.enabled)
    setKeywords((cfg.keywords || []).join(', '))
    setDigestEnabled(cfg.digest_enabled !== false)
    setDigestHour(typeof cfg.digest_hour === 'number' ? cfg.digest_hour : 9)
    setAlertMention(cfg.alert_on_bot_mention !== false)
    setAlertKeywords(cfg.alert_on_keywords !== false)
    setWatchAll(cfg.watch_all_groups !== false)
  }, [data])

  const saveMutation = useMutation({
    mutationFn: () =>
      telegramApi.updateGroupWatch(accountId, {
        enabled,
        keywords: keywords.split(',').map((k) => k.trim()).filter(Boolean),
        digest_enabled: digestEnabled,
        digest_hour: digestHour,
        alert_on_bot_mention: alertMention,
        alert_on_keywords: alertKeywords,
        watch_all_groups: watchAll,
        owner_chat_id: data?.data?.owner_chat_id ?? null,
        owner_username: data?.data?.owner_username ?? null,
        watched_chats: data?.data?.watched_chats || [],
        forward_alert_messages: !!data?.data?.forward_alert_messages,
        digest_max_messages: data?.data?.digest_max_messages || 40,
        auto_reply_groups_only_when_mentioned:
          data?.data?.auto_reply_groups_only_when_mentioned !== false,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telegram-group-watch', accountId] })
    },
  })

  const digestMutation = useMutation({
    mutationFn: () => telegramApi.digestNow(accountId),
  })

  const linksMutation = useMutation({
    mutationFn: () => telegramApi.setupGroupWatchLinks(accountId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telegram-group-watch', accountId] })
      queryClient.invalidateQueries({ queryKey: ['telegram-group-watch-activity', accountId] })
    },
  })

  const cfg = data?.data
  const act = activity?.data
  const setupLinks = linksMutation.data?.data?.links
  const checklist = linksMutation.data?.data?.checklist || []

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Bell className="h-4 w-4" />
          Group watch (stay updated)
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              One-tap Telegram setup (Bot API cannot join groups or change BotFather privacy by itself).
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={linksMutation.isPending}
                onClick={() => linksMutation.mutate()}
              >
                Prepare Telegram links
              </Button>
              {setupLinks?.link_owner && (
                <a href={setupLinks.link_owner} target="_blank" rel="noreferrer">
                  <Button type="button" size="sm">1. Link my DM</Button>
                </a>
              )}
              {setupLinks?.add_to_group && (
                <a href={setupLinks.add_to_group} target="_blank" rel="noreferrer">
                  <Button type="button" size="sm" variant="secondary">2. Add to group</Button>
                </a>
              )}
              {setupLinks?.botfather_privacy && (
                <a href={setupLinks.botfather_privacy} target="_blank" rel="noreferrer">
                  <Button type="button" size="sm" variant="outline">3. BotFather privacy</Button>
                </a>
              )}
            </div>
            {checklist.length > 0 && (
              <ul className="text-xs text-muted-foreground space-y-1">
                {checklist.map((item: { id: string; done: boolean; action: string; url?: string }) => (
                  <li key={item.id}>
                    {item.done ? '✓' : '○'} {item.action}
                    {item.url ? (
                      <>
                        {' '}
                        <a className="text-primary underline" href={item.url} target="_blank" rel="noreferrer">
                          open
                        </a>
                      </>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            <div className="text-sm space-y-1">
              <p>
                Owner linked:{' '}
                {cfg?.owner_chat_id ? (
                  <span className="text-green-600">yes ({cfg.owner_chat_id})</span>
                ) : (
                  <span className="text-amber-600">no — tap “Link my DM”</span>
                )}
              </p>
              <p>
                Watched chats: {(cfg?.watched_chats || []).length}
                {act?.chats?.length
                  ? ` · buffered in ${act.chats.filter((c: { buffered_count: number }) => c.buffered_count > 0).length}`
                  : ''}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={enabled} onCheckedChange={setEnabled} />
              <span className="text-sm font-medium">Enable group watch</span>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={watchAll} onCheckedChange={setWatchAll} />
              <span className="text-sm">Watch all groups the bot joins</span>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={alertMention} onCheckedChange={setAlertMention} />
              <span className="text-sm">Alert on @bot mention</span>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={alertKeywords} onCheckedChange={setAlertKeywords} />
              <span className="text-sm">Alert on keywords</span>
            </div>
            <div>
              <label className="text-sm font-medium">Keywords (comma-separated)</label>
              <Input
                value={keywords}
                onChange={(e) => setKeywords(e.target.value)}
                className="mt-1"
                placeholder="price, τιμή, help, cloudless"
              />
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={digestEnabled} onCheckedChange={setDigestEnabled} />
              <span className="text-sm">Daily digest</span>
            </div>
            <div>
              <label className="text-sm font-medium">Digest hour (Europe/Athens)</label>
              <Input
                type="number"
                min={0}
                max={23}
                value={digestHour}
                onChange={(e) => setDigestHour(Number(e.target.value))}
                className="mt-1 w-24"
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => saveMutation.mutate()}
                disabled={saveMutation.isPending}
              >
                Save group watch
              </Button>
              <Button
                variant="outline"
                onClick={() => digestMutation.mutate()}
                disabled={digestMutation.isPending || !cfg?.owner_chat_id}
              >
                Send digest now
              </Button>
            </div>
            {saveMutation.isSuccess && (
              <span className="text-sm text-green-600">Saved</span>
            )}
            {digestMutation.isSuccess && (
              <span className="text-sm text-green-600 ml-2">
                Digest sent: {digestMutation.data?.data?.sent ?? 0} chat(s)
              </span>
            )}
            {digestMutation.isError && (
              <p className="text-sm text-destructive">
                {getErrorMessage(digestMutation.error)}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function ConnectBotCard({ onConnected }: { onConnected: (id: string) => void }) {
  const [token, setToken] = useState('')
  const [setWebhook, setSetWebhook] = useState(true)
  const mutation = useMutation({
    mutationFn: () => telegramApi.connect({ bot_token: token, set_webhook: setWebhook }),
    onSuccess: (resp) => {
      const id = resp.data?.account_id
      if (id) onConnected(id)
    },
  })

  return (
    <form
      className="mt-4 space-y-3 max-w-xl"
      onSubmit={(e) => {
        e.preventDefault()
        mutation.mutate()
      }}
    >
      <div>
        <label className="text-sm font-medium">BotFather token</label>
        <Input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="123456:ABC-DEF..."
          className="mt-1 font-mono text-sm"
          required
        />
      </div>
      <div className="flex items-center gap-2">
        <Switch checked={setWebhook} onCheckedChange={setSetWebhook} />
        <span className="text-sm">Register HTTPS webhook after connect</span>
      </div>
      <Button type="submit" disabled={mutation.isPending || !token.trim()}>
        {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
        Connect bot
      </Button>
      {mutation.isError && (
        <p className="text-sm text-red-600">{getErrorMessage(mutation.error)}</p>
      )}
      {mutation.isSuccess && (
        <p className="text-sm text-green-600">Bot connected</p>
      )}
    </form>
  )
}

function SetupStatusCard({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['telegram-setup', accountId],
    queryFn: () => telegramApi.getSetupStatus(accountId),
  })
  const webhookMutation = useMutation({
    mutationFn: () => telegramApi.setupWebhook(accountId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telegram-setup', accountId] })
    },
  })

  const status = data?.data

  return (
    <Card>
      <CardHeader className="pb-3 flex flex-row items-center justify-between">
        <CardTitle className="text-sm flex items-center gap-2">
          <Settings className="h-4 w-4" />
          Setup status
        </CardTitle>
        <Button variant="ghost" size="sm" onClick={() => refetch()}>
          <RefreshCw className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <>
            <StatusRow ok={!!status?.credentials_configured} label="Credentials stored" />
            <StatusRow ok={!!status?.token_valid} label="Token valid (getMe)" />
            <StatusRow ok={!!status?.webhook_set} label="Webhook registered" />
            <StatusRow ok={!!status?.can_send_messages} label="Can send messages" />
            {status?.bot_username && (
              <p className="text-sm text-muted-foreground">
                Bot: @{status.bot_username}
                {status.webhook_url ? (
                  <>
                    <br />
                    Webhook: <code className="text-xs break-all">{status.webhook_url}</code>
                  </>
                ) : null}
              </p>
            )}
            {status?.next_step && status.next_step !== 'setup_complete' && (
              <p className="text-sm text-amber-700 dark:text-amber-400">Next: {status.next_step}</p>
            )}
            <p className="text-xs text-muted-foreground">{status?.note}</p>
            <Button
              size="sm"
              onClick={() => webhookMutation.mutate()}
              disabled={webhookMutation.isPending}
            >
              {webhookMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : null}
              Setup webhook
            </Button>
            {webhookMutation.isError && (
              <p className="text-sm text-red-600">{getErrorMessage(webhookMutation.error)}</p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function StatusRow({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      {ok ? (
        <CheckCircle2 className="h-4 w-4 text-green-600" />
      ) : (
        <XCircle className="h-4 w-4 text-muted-foreground" />
      )}
      {label}
    </div>
  )
}

function CredentialsCard({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const [token, setToken] = useState('')
  const [setWebhook, setSetWebhook] = useState(false)
  const mutation = useMutation({
    mutationFn: () =>
      telegramApi.updateCredentials(accountId, {
        bot_token: token,
        set_webhook: setWebhook,
      }),
    onSuccess: () => {
      setToken('')
      queryClient.invalidateQueries({ queryKey: ['telegram-setup', accountId] })
      queryClient.invalidateQueries({ queryKey: ['telegram-accounts'] })
    },
  })

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">Bot credentials</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-3 max-w-xl"
          onSubmit={(e) => {
            e.preventDefault()
            mutation.mutate()
          }}
        >
          <div>
            <label className="text-sm font-medium">Update BotFather token</label>
            <Input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="123456:ABC-DEF..."
              className="mt-1 font-mono text-sm"
              required
            />
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={setWebhook} onCheckedChange={setSetWebhook} />
            <span className="text-sm">Also re-register webhook</span>
          </div>
          <Button type="submit" disabled={mutation.isPending || !token.trim()}>
            Save credentials
          </Button>
          {mutation.isError && (
            <p className="text-sm text-red-600">{getErrorMessage(mutation.error)}</p>
          )}
          {mutation.isSuccess && (
            <p className="text-sm text-green-600">Credentials updated</p>
          )}
        </form>
      </CardContent>
    </Card>
  )
}

function SendMessageCard({ accountId }: { accountId: string }) {
  const [chatId, setChatId] = useState('')
  const [text, setText] = useState('')
  const mutation = useMutation({
    mutationFn: () =>
      telegramApi.sendMessage(accountId, {
        chat_id: /^\d+$/.test(chatId.trim()) ? Number(chatId.trim()) : chatId.trim(),
        text,
      }),
  })

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <MessageSquare className="h-4 w-4" />
          Send message
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-3 max-w-xl"
          onSubmit={(e) => {
            e.preventDefault()
            mutation.mutate()
          }}
        >
          <div>
            <label className="text-sm font-medium">Chat ID</label>
            <Input
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
              placeholder="User must message the bot first"
              className="mt-1"
              required
            />
            <p className="text-xs text-muted-foreground mt-1">
              Bots cannot start conversations — get chat_id from an inbound update after the user opens the bot.
            </p>
          </div>
          <div>
            <label className="text-sm font-medium">Text</label>
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              className="mt-1"
              required
              maxLength={4096}
            />
          </div>
          <Button type="submit" disabled={mutation.isPending || !chatId || !text}>
            {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Send className="h-4 w-4 mr-2" />}
            Send
          </Button>
          {mutation.isError && (
            <p className="text-sm text-red-600">{getErrorMessage(mutation.error)}</p>
          )}
          {mutation.isSuccess && (
            <p className="text-sm text-green-600">Message sent</p>
          )}
        </form>
      </CardContent>
    </Card>
  )
}

function AutoReplyCard({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['telegram-auto-reply', accountId],
    queryFn: () => telegramApi.getAutoReply(accountId),
  })
  const [enabled, setEnabled] = useState(false)
  const [systemPrompt, setSystemPrompt] = useState('')
  const [fallbackText, setFallbackText] = useState('')

  useEffect(() => {
    if (data?.data) {
      setEnabled(!!data.data.enabled)
      setSystemPrompt(data.data.system_prompt || '')
      setFallbackText(data.data.fallback_text || '')
    }
  }, [data])

  const mutation = useMutation({
    mutationFn: () =>
      telegramApi.updateAutoReply(accountId, {
        enabled,
        system_prompt: systemPrompt || undefined,
        fallback_text: fallbackText || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telegram-auto-reply', accountId] })
    },
  })

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
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault()
              mutation.mutate()
            }}
          >
            <div className="flex items-center gap-2">
              <Switch checked={enabled} onCheckedChange={setEnabled} />
              <span className="text-sm font-medium">Enable AI auto-reply</span>
            </div>
            <div>
              <label className="text-sm font-medium">System prompt</label>
              <Textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">Fallback text</label>
              <Input
                value={fallbackText}
                onChange={(e) => setFallbackText(e.target.value)}
                className="mt-1"
              />
            </div>
            <Button type="submit" disabled={mutation.isPending}>
              Save auto-reply
            </Button>
            {mutation.isSuccess && (
              <span className="text-sm text-green-600 ml-2">Saved</span>
            )}
          </form>
        )}
      </CardContent>
    </Card>
  )
}

function BotCard({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['telegram-bot', accountId],
    queryFn: () => telegramApi.getBot(accountId),
  })
  const createMutation = useMutation({
    mutationFn: () =>
      telegramApi.createBot(accountId, {
        name: 'Cloudless Assistant',
        personality: 'professional_friendly',
        language: 'auto',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telegram-bot', accountId] })
      queryClient.invalidateQueries({ queryKey: ['telegram-auto-reply', accountId] })
    },
  })
  const activateMutation = useMutation({
    mutationFn: () => telegramApi.activateBot(accountId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['telegram-bot', accountId] }),
  })
  const deactivateMutation = useMutation({
    mutationFn: () => telegramApi.deactivateBot(accountId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['telegram-bot', accountId] }),
  })

  const exists = !!data?.data?.exists
  const bot = data?.data?.bot

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Bot className="h-4 w-4" />
          Bot persona
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : exists ? (
          <>
            <p className="text-sm">
              <strong>{bot?.name || 'Bot'}</strong>
              {' — '}
              {bot?.enabled ? 'active' : 'inactive'}
            </p>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => activateMutation.mutate()}
                disabled={activateMutation.isPending || !!bot?.enabled}
              >
                Activate
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => deactivateMutation.mutate()}
                disabled={deactivateMutation.isPending || !bot?.enabled}
              >
                Deactivate
              </Button>
            </div>
          </>
        ) : (
          <Button
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
          >
            {createMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : null}
            Create default bot
          </Button>
        )}
        {createMutation.isError && (
          <p className="text-sm text-red-600">{getErrorMessage(createMutation.error)}</p>
        )}
      </CardContent>
    </Card>
  )
}
