'use client'

import { useState, useEffect, useCallback } from 'react'
import { MessageCircle, Send, Settings, Bot, RefreshCw, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Textarea } from '@/components/ui/Textarea'
import { messengerApi } from '@/services/api'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'

interface Conversation {
  id: string
  snippet: string | null
  updated_time: string | null
  message_count: number | null
  unread_count: number | null
  participants: { id?: string; name?: string }[] | null
}

interface Message {
  id: string
  message: string | null
  from_id: string | null
  created_time: string | null
}

interface MessengerInboxProps {
  accountId: string
}

export function MessengerInbox({ accountId }: MessengerInboxProps) {
  const [selectedConversation, setSelectedConversation] = useState<string | null>(null)
  const [replyText, setReplyText] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const queryClient = useQueryClient()

  // Fetch conversations
  const { data: conversations, isLoading: loadingConvos } = useQuery({
    queryKey: ['messenger-conversations', accountId],
    queryFn: () => messengerApi.getConversations(accountId),
    enabled: !!accountId,
    refetchInterval: 30000,
  })

  // Fetch messages for selected conversation
  const { data: messages, isLoading: loadingMessages } = useQuery({
    queryKey: ['messenger-messages', accountId, selectedConversation],
    queryFn: () => messengerApi.getConversationMessages(accountId, selectedConversation!),
    enabled: !!selectedConversation,
    refetchInterval: 10000,
  })

  // Send message mutation
  const sendMutation = useMutation({
    mutationFn: (data: { recipient_psid: string; text: string }) =>
      messengerApi.sendMessage(accountId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messenger-messages', accountId, selectedConversation] })
      queryClient.invalidateQueries({ queryKey: ['messenger-conversations', accountId] })
      setReplyText('')
      toast.success('Message sent')
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : 'Failed to send message'
      toast.error(msg)
    },
  })

  // Setup mutation
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

  // Get recipient PSID from conversation participants
  const getRecipientPsid = useCallback((conv: Conversation | undefined) => {
    if (!conv?.participants) return ''
    // The participant that's not the Page is the recipient
    const participant = conv.participants.find((p) => p.id && p.id !== accountId)
    return participant?.id || ''
  }, [accountId])

  const handleSend = () => {
    if (!replyText.trim() || !selectedConversation) return
    const conv = conversations?.data?.find((c: Conversation) => c.id === selectedConversation)
    const psid = getRecipientPsid(conv)
    if (!psid) {
      toast.error('Could not determine recipient PSID')
      return
    }
    sendMutation.mutate({ recipient_psid: psid, text: replyText.trim() })
  }

  return (
    <div className="space-y-4">
      {/* Setup button */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold flex items-center gap-2">
          <MessageCircle className="h-5 w-5" />
          Messenger Inbox
        </h3>
        <div className="flex gap-2">
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
        </div>
      </div>

      {showSettings && <AutoReplySettings accountId={accountId} />}

      {/* Inbox layout */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 h-[500px]">
        {/* Conversation list */}
        <Card className="md:col-span-1 overflow-hidden">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Conversations</CardTitle>
          </CardHeader>
          <CardContent className="p-0 overflow-y-auto h-[420px]">
            {loadingConvos ? (
              <div className="flex items-center justify-center p-8">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : conversations?.data?.length === 0 ? (
              <p className="text-sm text-muted-foreground p-4 text-center">No conversations yet</p>
            ) : (
              <div className="space-y-1">
                {conversations?.data?.map((conv: Conversation) => (
                  <button
                    key={conv.id}
                    onClick={() => setSelectedConversation(conv.id)}
                    className={`w-full text-left p-3 hover:bg-accent border-b transition-colors ${
                      selectedConversation === conv.id ? 'bg-accent' : ''
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium truncate">
                        {conv.participants?.find((p: { id?: string; name?: string }) => p.id !== accountId)?.name || 'Unknown'}
                      </span>
                      {conv.unread_count ? (
                        <span className="text-xs bg-blue-500 text-white rounded-full px-2 py-0.5">
                          {conv.unread_count}
                        </span>
                      ) : null}
                    </div>
                    <p className="text-xs text-muted-foreground truncate">{conv.snippet}</p>
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Message thread */}
        <Card className="md:col-span-2 overflow-hidden flex flex-col">
          {selectedConversation ? (
            <>
              <CardHeader className="pb-2 border-b">
                <CardTitle className="text-sm">
                  {conversations?.data?.find((c: Conversation) => c.id === selectedConversation)?.participants?.find((p: { id?: string; name?: string }) => p.id !== accountId)?.name || 'Conversation'}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0 overflow-y-auto flex-1">
                {loadingMessages ? (
                  <div className="flex items-center justify-center p-8">
                    <Loader2 className="h-5 w-5 animate-spin" />
                  </div>
                ) : (
                  <div className="space-y-2 p-4">
                    {messages?.data?.map((msg: Message) => (
                      <div
                        key={msg.id}
                        className={`flex ${msg.from_id === accountId ? 'justify-end' : 'justify-start'}`}
                      >
                        <div
                          className={`max-w-[70%] rounded-lg p-2 text-sm ${
                            msg.from_id === accountId
                              ? 'bg-blue-500 text-white'
                              : 'bg-accent'
                          }`}
                        >
                          <p>{msg.message}</p>
                          {msg.created_time && (
                            <p className="text-xs opacity-60 mt-1">
                              {new Date(msg.created_time).toLocaleString()}
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

// Auto-reply settings component
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
