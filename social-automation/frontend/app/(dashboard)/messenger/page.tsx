'use client'

import { useState, useEffect } from 'react'
import { MessageCircle, Server, Activity, RefreshCw, Loader2, AlertCircle, CheckCircle2, XCircle, User, Bot, Globe, Lock, Unlock } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { MessengerInbox } from '@/components/ui/MessengerInbox'
import { BotBuilder } from '@/components/ui/BotBuilder'
import { messengerApi, accountsApi } from '@/services/api'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'

export default function MessengerPage() {
  const [selectedAccountId, setSelectedAccountId] = useState<string>('')
  const queryClient = useQueryClient()

  // Fetch Facebook Page accounts
  const { data: accountsData, isLoading: loadingAccounts } = useQuery({
    queryKey: ['facebook-page-accounts'],
    queryFn: () => accountsApi.list(),
  })

  // Fetch sidecar status
  const { data: sidecarData, isLoading: loadingSidecar, refetch: refetchSidecar } = useQuery({
    queryKey: ['messenger-sidecar-status'],
    queryFn: () => messengerApi.getSidecarStatus(),
    refetchInterval: 30000,
  })

  // Fetch browser orchestrator status
  const { data: orchestratorData, isLoading: loadingOrchestrator, refetch: refetchOrchestrator } = useQuery({
    queryKey: ['browser-orchestrator-status'],
    queryFn: () => messengerApi.getOrchestratorStatus(),
    refetchInterval: 10000,
  })

  // Force-release lock mutation
  const releaseLockMutation = useMutation({
    mutationFn: () => messengerApi.releaseOrchestratorLock(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['browser-orchestrator-status'] })
    },
  })

  // Auto-select the first Facebook Page account
  useEffect(() => {
    if (!selectedAccountId && accountsData?.data) {
      const accounts = accountsData.data
      const fbPage = accounts.find(
        (a: { platform: string; account_type?: string; status: string }) =>
          a.platform === 'facebook' && a.status === 'active'
      )
      if (fbPage) {
        setSelectedAccountId(fbPage.id)
      }
    }
  }, [accountsData, selectedAccountId])

  const fbPageAccounts = (accountsData?.data || []).filter(
    (a: { platform: string; status: string; account_type?: string }) =>
      a.platform === 'facebook' && a.status === 'active' && a.account_type === 'page'
  )

  const fbPersonalAccounts = (accountsData?.data || []).filter(
    (a: { platform: string; status: string; account_type?: string }) =>
      a.platform === 'facebook' && a.status === 'active' && a.account_type === 'user'
  )

  // Determine the account type of the currently selected account
  const selectedAccount = (accountsData?.data || []).find(
    (a: { id: string; account_type?: string }) => a.id === selectedAccountId
  )
  const selectedAccountType = selectedAccount?.account_type === 'user' ? 'user' : 'page'

  const sidecarStatus = sidecarData?.data
  const isOnline = sidecarStatus?.status === 'online'
  const isOffline = sidecarStatus?.status === 'offline' || sidecarStatus?.status === 'error'
  const isNotConfigured = sidecarStatus?.status === 'not_configured'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <MessageCircle className="h-6 w-6" />
          Messenger
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Manage Facebook Messenger for your Pages and personal account — send/receive messages, AI auto-reply, webhook events
        </p>
      </div>

      {/* Sidecar status */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center justify-between">
            <span className="flex items-center gap-2">
              <Server className="h-4 w-4" />
              Webhook Sidecar
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refetchSidecar()}
              disabled={loadingSidecar}
            >
              {loadingSidecar ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {loadingSidecar ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Checking sidecar status...
            </div>
          ) : isOnline ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm">
                <CheckCircle2 className="h-4 w-4 text-green-500" />
                <span className="font-medium text-green-600">Online</span>
                <span className="text-muted-foreground">— {sidecarStatus?.url}</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
                <StatCard
                  label="Events Received"
                  value={sidecarStatus?.stats?.events_received ?? 0}
                  icon={<Activity className="h-3 w-3" />}
                />
                <StatCard
                  label="Events Processed"
                  value={sidecarStatus?.stats?.events_processed ?? 0}
                  icon={<CheckCircle2 className="h-3 w-3" />}
                />
                <StatCard
                  label="Auto-Replies Sent"
                  value={sidecarStatus?.stats?.auto_replies_sent ?? 0}
                  icon={<MessageCircle className="h-3 w-3" />}
                />
                <StatCard
                  label="Errors"
                  value={sidecarStatus?.stats?.errors ?? 0}
                  icon={<AlertCircle className="h-3 w-3" />}
                  error={sidecarStatus?.stats?.errors > 0}
                />
              </div>
            </div>
          ) : isNotConfigured ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <AlertCircle className="h-4 w-4 text-yellow-500" />
              <span>Sidecar not configured — webhook events are processed inline by the API</span>
            </div>
          ) : isOffline ? (
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-sm">
                <XCircle className="h-4 w-4 text-red-500" />
                <span className="font-medium text-red-600">Offline</span>
              </div>
              <p className="text-xs text-muted-foreground">{sidecarStatus?.message}</p>
              <p className="text-xs text-muted-foreground mt-1">
                Webhook events will fall back to inline processing in the API.
              </p>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* Browser Bridge Orchestrator */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center justify-between">
            <span className="flex items-center gap-2">
              <Globe className="h-4 w-4" />
              Browser Bridge Orchestrator
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refetchOrchestrator()}
              disabled={loadingOrchestrator}
            >
              {loadingOrchestrator ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {loadingOrchestrator ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Checking orchestrator status...
            </div>
          ) : orchestratorData?.data ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm">
                {orchestratorData.data.lock_held ? (
                  <>
                    <Lock className="h-4 w-4 text-blue-500" />
                    <span className="font-medium text-blue-600">
                      Browser held by {orchestratorData.data.current_platform}
                    </span>
                  </>
                ) : (
                  <>
                    <Unlock className="h-4 w-4 text-green-500" />
                    <span className="font-medium text-green-600">Browser idle</span>
                  </>
                )}
              </div>
              {orchestratorData.data.queue_length > 0 && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {orchestratorData.data.queue_length} worker(s) waiting in queue
                </div>
              )}
              <div className="flex items-center gap-2 mt-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => releaseLockMutation.mutate()}
                  disabled={releaseLockMutation.isPending || !orchestratorData.data.lock_held}
                >
                  {releaseLockMutation.isPending ? (
                    <Loader2 className="h-3 w-3 animate-spin mr-1" />
                  ) : (
                    <Unlock className="h-3 w-3 mr-1" />
                  )}
                  Force Release Lock
                </Button>
                <span className="text-xs text-muted-foreground">
                  Use when a worker crashed while holding the browser lock
                </span>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <AlertCircle className="h-4 w-4 text-yellow-500" />
              <span>{orchestratorData?.data?.message || 'Orchestrator status unavailable'}</span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Account selector */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Select Facebook Account</CardTitle>
        </CardHeader>
        <CardContent>
          {loadingAccounts ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading accounts...
            </div>
          ) : fbPageAccounts.length === 0 && fbPersonalAccounts.length === 0 ? (
            <div className="text-sm text-muted-foreground space-y-2">
              <p>No Facebook accounts connected.</p>
              <p>
                Connect a Facebook account in the{' '}
                <a href="/accounts" className="text-primary underline">Accounts</a> page first,
                then use the Setup button below to enable Messenger.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {/* Page accounts (Messenger Platform API) */}
              {fbPageAccounts.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-xs text-muted-foreground font-medium">Pages (Messenger Platform API)</div>
                  <div className="flex flex-wrap gap-2">
                    {fbPageAccounts.map((account: { id: string; display_name?: string; account_id?: string; meta_data?: { messenger_setup?: { subscribed?: boolean } } }) => {
                      const isSetup = account.meta_data?.messenger_setup?.subscribed === true
                      return (
                        <button
                          key={account.id}
                          onClick={() => setSelectedAccountId(account.id)}
                          className={`px-4 py-2 rounded-lg border text-sm transition-colors flex items-center gap-2 ${
                            selectedAccountId === account.id
                              ? 'bg-primary text-primary-foreground border-primary'
                              : 'bg-card hover:bg-accent border-border'
                          }`}
                        >
                          {account.display_name || account.account_id || 'Facebook Page'}
                          {isSetup && (
                            <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                              selectedAccountId === account.id
                                ? 'bg-primary-foreground/20 text-primary-foreground'
                                : 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'
                            }`}>
                              ●
                            </span>
                          )}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Personal accounts (browser bridge) */}
              {fbPersonalAccounts.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-xs text-muted-foreground font-medium">
                    Personal (Browser Bridge — requires noVNC login)
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {fbPersonalAccounts.map((account: { id: string; display_name?: string; account_id?: string }) => (
                      <button
                        key={account.id}
                        onClick={() => setSelectedAccountId(account.id)}
                        className={`px-4 py-2 rounded-lg border text-sm transition-colors flex items-center gap-2 ${
                          selectedAccountId === account.id
                            ? 'bg-primary text-primary-foreground border-primary'
                            : 'bg-card hover:bg-accent border-border'
                        }`}
                      >
                        <User className="h-3 w-3" />
                        {account.display_name || account.account_id || 'Personal Account'}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Bot Builder — only render when account type is confirmed */}
      {selectedAccountId && selectedAccount && (
        <BotBuilder accountId={selectedAccountId} accountType={selectedAccountType} />
      )}

      {/* Messenger Inbox — only render when account type is confirmed */}
      {selectedAccountId && selectedAccount ? (
        <MessengerInbox accountId={selectedAccountId} accountType={selectedAccountType} />
      ) : (
        <Card>
          <CardContent className="flex items-center justify-center h-[200px] text-muted-foreground text-sm">
            {selectedAccountId ? 'Loading account details...' : 'Select a Facebook account to view Messenger inbox'}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function StatCard({
  label,
  value,
  icon,
  error,
}: {
  label: string
  value: number
  icon: React.ReactNode
  error?: boolean
}) {
  return (
    <div className={`p-3 rounded-lg border ${error ? 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950' : 'bg-accent/50'}`}>
      <div className="flex items-center gap-1 text-xs text-muted-foreground mb-1">
        {icon}
        {label}
      </div>
      <div className={`text-lg font-bold ${error ? 'text-red-600' : ''}`}>{value}</div>
    </div>
  )
}
