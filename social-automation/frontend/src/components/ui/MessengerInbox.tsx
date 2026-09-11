'use client'

import { useState, useEffect, useCallback } from 'react'
import { MessageCircle, Send, Settings, Bot, RefreshCw, Loader2, AlertCircle, User, Monitor } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Textarea } from '@/components/ui/Textarea'
import { messengerApi } from '@/services/api'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'

// ── Page Messenger types (Graph API) ──────────────────────────────
interface PageConversation {
  id: string
  snippet: string | null
  updated_time: string | null
  message_count: number | null
  unread_count: number | null
  participants: { id?: string; name?: string }[] | null
}

interface PageMessage {
  id: string
  message: string | null
  from_id: string | null
  created_time: string | null
}

// ── Personal Messenger types (browser bridge) ─────────────────────
interface PersonalConversation {
  name: string
  preview: string
  thread_id: string | null
  url: string
  unread: boolean
  e2ee?: boolean
}

interface PersonalMessage {
  text: string
  sender: 'me' | 'them'
  timestamp: string | null
}

interface MessengerInboxProps {
  accountId: string
  accountType: 'page' | 'user'
}

export function MessengerInbox({ accountId, accountType }: MessengerInboxProps) {
  const [selectedThread, setSelectedThread] = useState<string | null>(null)
  const [selectedThreadIsE2ee, setSelectedThreadIsE2ee] = useState(false)
  const [replyText, setReplyText] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const queryClient = useQueryClient()

  const isPersonal = accountType === 'user'

  // ── Fetch conversations ───────────────────────────────────────────
  const { data: conversationsData, isLoading: loadingConvos, error: convosError } = useQuery({
    queryKey: ['messenger-conversations', accountId, accountType],
    queryFn: () =>
      isPersonal
        ? messengerApi.getPersonalConversations(accountId)
        : messengerApi.getConversations(accountId),
    enabled: !!accountId,
    refetchInterval: 30000,
    retry: 1,
  })

  // ── Fetch messages for selected thread ───────────────────────────
  const { data: messagesData, isLoading: loadingMessages, error: messagesError } = useQuery({
    queryKey: ['messenger-messages', accountId, accountType, selectedThread, selectedThreadIsE2ee],
    queryFn: () =>
      isPersonal
        ? messengerApi.getPersonalMessages(accountId, selectedThread!, selectedThreadIsE2ee)
        : messengerApi.getConversationMessages(accountId, selectedThread!),
    enabled: !!selectedThread,
    refetchInterval: 10000,
    retry: 1,
  })

  // ── Send message mutation ────────────────────────────────────────
  const sendMutation = useMutation({
    mutationFn: async (data: { text: string; threadId: string; recipientPsid?: string; isE2ee?: boolean }) => {
      if (isPersonal) {
        return messengerApi.sendPersonalMessage(accountId, {
          thread_id: data.threadId,
          text: data.text,
          is_e2ee: data.isE2ee,
        })
      }
      return messengerApi.sendMessage(accountId, {
        recipient_psid: data.recipientPsid || data.threadId,
        text: data.text,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messenger-messages', accountId, accountType, selectedThread, selectedThreadIsE2ee] })
      queryClient.invalidateQueries({ queryKey: ['messenger-conversations', accountId, accountType] })
      setReplyText('')
      toast.success('Message sent')
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : 'Failed to send message'
      toast.error(msg)
    },
  })

  // ── Setup mutation (Page only) ───────────────────────────────────
  const setupMutation = useMutation({
    mutationFn: () => messengerApi.setup(accountId),
    onSuccess: () => {
      toast.success('Messenger setup complete — Page subscribed, greeting & menu configured')
      queryClient.invalidateQueries({ queryKey: ['messenger-profile', accountId] })
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : 'Setup failed'
      toast.error(msg)
    },
  })

  // ── Get recipient PSID from Page conversation participants ───────
  const getRecipientPsid = useCallback((conv: PageConversation | undefined) => {
    if (!conv?.participants) return ''
    const participant = conv.participants.find((p) => p.id && p.id !== accountId)
    return participant?.id || ''
  }, [accountId])

  const handleSend = () => {
    if (!replyText.trim() || !selectedThread) return
    if (isPersonal) {
      sendMutation.mutate({ text: replyText.trim(), threadId: selectedThread, isE2ee: selectedThreadIsE2ee })
    } else {
      const conv = (conversationsData?.data as PageConversation[] | undefined)?.find(
        (c) => c.id === selectedThread
      )
      const psid = getRecipientPsid(conv)
      if (!psid) {
        toast.error('Could not determine recipient PSID')
        return
      }
      sendMutation.mutate({ text: replyText.trim(), threadId: selectedThread, recipientPsid: psid })
    }
  }

  // ── Normalize conversations for rendering ────────────────────────
  const normalizedConvos = (() => {
    if (isPersonal) {
      const raw = conversationsData?.data as { conversations?: PersonalConversation[] } | undefined
      return (raw?.conversations || []).filter((c) => c.thread_id).map((c) => ({
        id: c.thread_id!,
        name: c.name,
        preview: c.preview,
        unread: c.unread,
        e2ee: c.e2ee || false,
      }))
    }
    const raw = conversationsData?.data as PageConversation[] | undefined
    return (raw || []).map((c) => ({
      id: c.id,
      name: c.participants?.find((p) => p.id !== accountId)?.name || 'Unknown',
      preview: c.snippet || '',
      unread: !!c.unread_count,
      e2ee: false,
    }))
  })()

  // ── Normalize messages for rendering ────────────────────────────
  const normalizedMessages = (() => {
    if (!messagesData?.data) return []
    if (isPersonal) {
      const raw = messagesData.data as { messages?: PersonalMessage[] }
      return (raw.messages || []).map((m, i) => ({
        id: `msg-${i}`,
        text: m.text,
        isMe: m.sender === 'me',
        timestamp: m.timestamp,
      }))
    }
    const raw = messagesData.data as PageMessage[]
    return (raw || []).map((m) => ({
      id: m.id,
      text: m.message || '',
      isMe: m.from_id === accountId,
      timestamp: m.created_time,
    }))
  })()

  // ── Selected conversation name ──────────────────────────────────
  const selectedConvoName = normalizedConvos.find((c) => c.id === selectedThread)?.name || 'Conversation'

  // ── Error display ────────────────────────────────────────────────
  const errorMsg = convosError
    ? (convosError as Error)?.message || 'Failed to load conversations'
    : null

  return (
    <div className="space-y-4">
      {/* Header + actions */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold flex items-center gap-2">
          <MessageCircle className="h-5 w-5" />
          {isPersonal ? 'Personal Messenger' : 'Page Messenger'} Inbox
        </h3>
        <div className="flex gap-2">
          {!isPersonal && (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setupMutation.mutate()}
                disabled={setupMutation.isPending}
              >
                {setupMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Settings className="h-4 w-4" />
                )}
                Setup Messenger
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowSettings(!showSettings)}
              >
                <Bot className="h-4 w-4" />
                Auto-Reply
              </Button>
            </>
          )}
          {isPersonal && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowSettings(!showSettings)}
            >
              <Bot className="h-4 w-4" />
              Auto-Reply
            </Button>
          )}
          {isPersonal && (
            <a
              href="http://localhost:6080/vnc.html"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary"
            >
              <Monitor className="h-3 w-3" />
              Open noVNC
            </a>
          )}
        </div>
      </div>

      {/* Personal account browser bridge notice */}
      {isPersonal && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-900 text-sm">
          <AlertCircle className="h-4 w-4 text-blue-500 mt-0.5 shrink-0" />
          <div className="space-y-1">
            <p className="font-medium text-blue-700 dark:text-blue-300">
              Personal Messenger uses browser automation
            </p>
            <p className="text-blue-600 dark:text-blue-400 text-xs">
              Requires a logged-in Facebook session via noVNC. If conversations don't load,
              open the noVNC viewer and log in to Facebook.
            </p>
          </div>
        </div>
      )}

      {/* Auto-reply settings (Page only) */}
      {!isPersonal && showSettings && <AutoReplySettings accountId={accountId} />}

      {/* Personal auto-reply settings */}
      {isPersonal && showSettings && <PersonalAutoReplySettings accountId={accountId} />}

      {/* Error banner */}
      {errorMsg && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 text-sm">
          <AlertCircle className="h-4 w-4 text-red-500 mt-0.5 shrink-0" />
          <div className="space-y-2">
            <p className="font-medium text-red-700 dark:text-red-300">Error loading conversations</p>
            <p className="text-red-600 dark:text-red-400 text-xs mt-1">{errorMsg}</p>
            {isPersonal && (
              <div className="text-red-600 dark:text-red-400 text-xs mt-2 space-y-2">
                <p>The browser session is not logged in. A login session has been started — open noVNC to log in to Facebook:</p>
                <a
                  href="http://localhost:6080/vnc.html"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md bg-red-600 text-white text-xs font-medium hover:bg-red-700 transition-colors"
                >
                  <Monitor className="h-3 w-3" />
                  Open noVNC & Log In
                </a>
                <p className="text-xs">After logging in, conversations will load automatically within 30 seconds.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Inbox layout */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 h-[500px]">
        {/* Conversation list */}
        <Card className="md:col-span-1 overflow-hidden">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <User className="h-3 w-3" />
              Conversations
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0 overflow-y-auto h-[420px]">
            {loadingConvos ? (
              <div className="flex items-center justify-center p-8">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : normalizedConvos.length === 0 ? (
              <p className="text-sm text-muted-foreground p-4 text-center">
                {isPersonal ? 'No conversations found (or browser session not logged in)' : 'No conversations yet'}
              </p>
            ) : (
              <div className="space-y-1">
                {normalizedConvos.map((conv) => (
                  <button
                    key={conv.id}
                    onClick={() => {
                      setSelectedThread(conv.id)
                      setSelectedThreadIsE2ee(conv.e2ee || false)
                    }}
                    className={`w-full text-left p-3 hover:bg-accent border-b transition-colors ${
                      selectedThread === conv.id ? 'bg-accent' : ''
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium truncate">{conv.name}</span>
                      {conv.unread && (
                        <span className="text-xs bg-blue-500 text-white rounded-full px-2 py-0.5">
                          ●
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground truncate">{conv.preview}</p>
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Message thread */}
        <Card className="md:col-span-2 overflow-hidden flex flex-col">
          {selectedThread ? (
            <>
              <CardHeader className="pb-2 border-b">
                <CardTitle className="text-sm">{selectedConvoName}</CardTitle>
              </CardHeader>
              <CardContent className="p-0 overflow-y-auto flex-1">
                {loadingMessages ? (
                  <div className="flex items-center justify-center p-8">
                    <Loader2 className="h-5 w-5 animate-spin" />
                  </div>
                ) : messagesError ? (
                  <div className="flex flex-col items-center justify-center p-8 text-center">
                    <AlertCircle className="h-8 w-8 text-red-400 mb-2" />
                    <p className="text-sm text-muted-foreground">
                      {isPersonal
                        ? 'Could not read messages. Browser session may have expired.'
                        : 'Could not load messages for this conversation.'}
                    </p>
                  </div>
                ) : normalizedMessages.length === 0 ? (
                  <div className="flex items-center justify-center p-8">
                    <p className="text-sm text-muted-foreground">No messages in this conversation</p>
                  </div>
                ) : (
                  <div className="space-y-2 p-4">
                    {normalizedMessages.map((msg) => (
                      <div
                        key={msg.id}
                        className={`flex ${msg.isMe ? 'justify-end' : 'justify-start'}`}
                      >
                        <div
                          className={`max-w-[70%] rounded-lg p-2 text-sm ${
                            msg.isMe
                              ? 'bg-blue-500 text-white'
                              : 'bg-accent'
                          }`}
                        >
                          <p>{msg.text}</p>
                          {msg.timestamp && (
                            <p className="text-xs opacity-60 mt-1">
                              {msg.timestamp}
                            </p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
              {/* Reply box */}
              <div className="p-3 border-t flex gap-2">
                <Textarea
                  value={replyText}
                  onChange={(e) => setReplyText(e.target.value)}
                  placeholder="Type a reply..."
                  className="min-h-[40px] max-h-[80px] resize-none"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      handleSend()
                    }
                  }}
                />
                <Button
                  onClick={handleSend}
                  disabled={!replyText.trim() || sendMutation.isPending}
                  size="icon"
                >
                  {sendMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4" />
                  )}
                </Button>
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center h-full text-muted-foreground">
              <p>Select a conversation to view messages</p>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

// Auto-reply settings component (Page only)
function AutoReplySettings({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const [config, setConfig] = useState({
    enabled: false,
    system_prompt: 'You are a helpful assistant for {page_name}. Reply concisely and professionally.',
    model: '@cf/meta/llama-3.1-8b-instruct',
    fallback_text: 'Thanks for your message! We\'ll get back to you soon.',
    max_tokens: 200,
  })

  // Load config
  const configQuery = useQuery({
    queryKey: ['messenger-auto-reply', accountId],
    queryFn: async () => {
      const resp = await messengerApi.getAutoReplyConfig(accountId)
      return resp.data
    },
    enabled: !!accountId,
  })

  useEffect(() => {
    if (configQuery.data) {
      setConfig(configQuery.data as typeof config)
    }
  }, [configQuery.data])

  const updateMutation = useMutation({
    mutationFn: (data: typeof config) => messengerApi.updateAutoReplyConfig(accountId, data),
    onSuccess: () => {
      toast.success('Auto-reply settings saved')
      queryClient.invalidateQueries({ queryKey: ['messenger-auto-reply', accountId] })
    },
    onError: () => toast.error('Failed to save settings'),
  })

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium flex items-center gap-2">
            <Bot className="h-4 w-4" />
            AI Auto-Reply
          </label>
          <input
            type="checkbox"
            checked={config.enabled}
            onChange={(e) => setConfig({ ...config, enabled: e.target.checked })}
            className="h-4 w-4"
          />
        </div>
        <div className="space-y-2">
          <label className="text-xs text-muted-foreground">System Prompt</label>
          <Textarea
            value={config.system_prompt}
            onChange={(e) => setConfig({ ...config, system_prompt: e.target.value })}
            className="min-h-[60px]"
          />
        </div>
        <div className="space-y-2">
          <label className="text-xs text-muted-foreground">Model (Cloudflare Workers AI)</label>
          <input
            type="text"
            value={config.model}
            onChange={(e) => setConfig({ ...config, model: e.target.value })}
            className="w-full p-2 border rounded text-sm"
          />
        </div>
        <div className="space-y-2">
          <label className="text-xs text-muted-foreground">Fallback Text</label>
          <input
            type="text"
            value={config.fallback_text}
            onChange={(e) => setConfig({ ...config, fallback_text: e.target.value })}
            className="w-full p-2 border rounded text-sm"
          />
        </div>
        <Button
          onClick={() => updateMutation.mutate(config)}
          disabled={updateMutation.isPending}
          size="sm"
        >
          {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Save Settings
        </Button>
      </CardContent>
    </Card>
  )
}

// Personal auto-reply settings component (browser bridge)
function PersonalAutoReplySettings({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient()
  const [config, setConfig] = useState({
    enabled: false,
    system_prompt: 'You are a helpful assistant for {page_name}. Reply concisely and professionally.',
    model: '@cf/meta/llama-3.1-8b-instruct',
    fallback_text: 'Thanks for your message! I\'ll get back to you soon.',
    max_tokens: 200,
  })
  const [lastChecked, setLastChecked] = useState<string | null>(null)

  const configQuery = useQuery({
    queryKey: ['personal-messenger-auto-reply', accountId],
    queryFn: async () => {
      const resp = await messengerApi.getPersonalAutoReply(accountId)
      return resp.data
    },
    enabled: !!accountId,
  })

  useEffect(() => {
    if (configQuery.data) {
      const data = configQuery.data as typeof config & { last_checked?: string }
      setConfig({
        enabled: data.enabled,
        system_prompt: data.system_prompt,
        model: data.model,
        fallback_text: data.fallback_text,
        max_tokens: data.max_tokens,
      })
      setLastChecked(data.last_checked || null)
    }
  }, [configQuery.data])

  const updateMutation = useMutation({
    mutationFn: (data: typeof config) => messengerApi.updatePersonalAutoReply(accountId, data),
    onSuccess: () => {
      toast.success('Personal auto-reply settings saved')
      queryClient.invalidateQueries({ queryKey: ['personal-messenger-auto-reply', accountId] })
    },
    onError: () => toast.error('Failed to save settings'),
  })

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium flex items-center gap-2">
            <Bot className="h-4 w-4" />
            AI Auto-Reply (Personal)
          </label>
          <input
            type="checkbox"
            checked={config.enabled}
            onChange={(e) => setConfig({ ...config, enabled: e.target.checked })}
            className="h-4 w-4"
          />
        </div>
        <div className="text-xs text-muted-foreground bg-blue-50 dark:bg-blue-950 p-2 rounded">
          When enabled, a Celery task polls your conversations every 2 minutes
          and sends AI-generated replies to new inbound messages via the browser bridge.
        </div>
        {lastChecked && (
          <div className="text-xs text-muted-foreground">
            Last checked: {new Date(lastChecked).toLocaleString()}
          </div>
        )}
        <div className="space-y-2">
          <label className="text-xs text-muted-foreground">System Prompt</label>
          <Textarea
            value={config.system_prompt}
            onChange={(e) => setConfig({ ...config, system_prompt: e.target.value })}
            className="min-h-[60px]"
          />
        </div>
        <div className="space-y-2">
          <label className="text-xs text-muted-foreground">Model (Cloudflare Workers AI)</label>
          <input
            type="text"
            value={config.model}
            onChange={(e) => setConfig({ ...config, model: e.target.value })}
            className="w-full p-2 border rounded text-sm"
          />
        </div>
        <div className="space-y-2">
          <label className="text-xs text-muted-foreground">Fallback Text</label>
          <input
            type="text"
            value={config.fallback_text}
            onChange={(e) => setConfig({ ...config, fallback_text: e.target.value })}
            className="w-full p-2 border rounded text-sm"
          />
        </div>
        <Button
          onClick={() => updateMutation.mutate(config)}
          disabled={updateMutation.isPending}
          size="sm"
        >
          {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Save Settings
        </Button>
      </CardContent>
    </Card>
  )
}
