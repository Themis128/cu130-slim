'use client'

import { useState, useEffect, useCallback } from 'react'
import {
  Activity,
  CheckCircle2,
  XCircle,
  Loader2,
  RefreshCw,
  Server,
  Layers,
  Image as ImageIcon,
  MonitorSmartphone,
  ShieldCheck,
  HeartPulse,
  ExternalLink,
  Linkedin,
  Facebook,
  Instagram,
  Music2,
  Twitter,
  Globe,
} from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import toast from 'react-hot-toast'
import { opsApi, type OpsConsoleResponse, type OpsConsoleAccount } from '@/services/api'

const PLATFORM_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  linkedin: Linkedin,
  facebook: Facebook,
  instagram: Instagram,
  tiktok: Music2,
  twitter: Twitter,
  threads: Globe,
}

const AUDIT_LABELS: Record<string, { label: string; tone: 'success' | 'warning' | 'destructive' | 'secondary' }> = {
  under_review: { label: 'Under review', tone: 'warning' },
  pending_review: { label: 'Under review', tone: 'warning' },
  approved: { label: 'Approved', tone: 'success' },
  rejected: { label: 'Rejected', tone: 'destructive' },
  not_submitted: { label: 'Not submitted', tone: 'secondary' },
}

function statusBadge(status: string) {
  if (status === 'active') return <Badge variant="default">active</Badge>
  return <Badge variant="destructive">{status}</Badge>
}

export default function OpsConsolePage() {
  const [data, setData] = useState<OpsConsoleResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [healing, setHealing] = useState(false)
  const [releasing, setReleasing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await opsApi.getConsole()
      setData(res.data)
    } catch {
      toast.error('Failed to load ops console')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const runSessionHeal = async () => {
    setHealing(true)
    try {
      await opsApi.sessionHeal()
      toast.success('Session heal sweep completed')
      await load()
    } catch {
      toast.error('Session heal failed')
    } finally {
      setHealing(false)
    }
  }

  const releaseLock = async () => {
    setReleasing(true)
    try {
      await opsApi.releaseBrowserLock()
      toast.success('Browser lock released')
      await load()
    } catch {
      toast.error('Release failed')
    } finally {
      setReleasing(false)
    }
  }

  const accountsByPlatform = (data?.accounts ?? []).reduce<Record<string, OpsConsoleAccount[]>>(
    (acc, a) => {
      ;(acc[a.platform] ??= []).push(a)
      return acc
    },
    {},
  )

  const queue = data?.publish_queue ?? {}
  const failedCount = queue.failed ?? 0
  const pendingCount = (queue.pending ?? 0) + (queue.processing ?? 0)
  const audit = data?.tiktok_audit
  const auditMeta = AUDIT_LABELS[String(audit?.status ?? '')] ?? {
    label: audit ? String(audit.status) : 'Unknown',
    tone: 'secondary' as const,
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Activity className="h-6 w-6" /> Ops Console
          </h1>
          <p className="text-sm text-muted-foreground">
            Platform health, connected accounts, publish queue and media pipeline
            {data?.checked_at && (
              <> — checked {new Date(data.checked_at).toLocaleTimeString()}</>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={runSessionHeal} disabled={healing}>
            {healing ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <HeartPulse className="h-4 w-4 mr-1" />}
            Heal sessions
          </Button>
        </div>
      </div>

      {/* Services */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Server className="h-5 w-5" /> Services</CardTitle>
          <CardDescription>Browser sidecars and the media render backend</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(data?.services ?? []).map((s) => (
              <div key={s.name} className="flex items-center justify-between rounded-lg border p-3">
                <span className="font-medium">{s.name}</span>
                <span className="flex items-center gap-1 text-sm">
                  {s.online ? (
                    <CheckCircle2 className="h-4 w-4 text-green-500" />
                  ) : (
                    <XCircle className="h-4 w-4 text-red-500" />
                  )}
                  {s.online ? 'online' : `offline (${s.detail})`}
                </span>
              </div>
            ))}
            {!data && loading && (
              <div className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</div>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Accounts */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><MonitorSmartphone className="h-5 w-5" /> Connected accounts</CardTitle>
            <CardDescription>{data?.accounts.length ?? 0} accounts across {Object.keys(accountsByPlatform).length} platforms</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {Object.entries(accountsByPlatform).map(([platform, accounts]) => {
              const Icon = PLATFORM_ICONS[platform] ?? Globe
              return (
                <div key={platform}>
                  <div className="mb-1 flex items-center gap-2 font-medium capitalize">
                    <Icon className="h-4 w-4" /> {platform}
                    <span className="text-xs text-muted-foreground">({accounts.length})</span>
                  </div>
                  <div className="space-y-1">
                    {accounts.map((a) => (
                      <div key={a.id} className="flex items-center justify-between rounded border px-3 py-1.5 text-sm">
                        <span>{a.display_name || a.username || a.id.slice(0, 8)}</span>
                        <span className="flex items-center gap-2">
                          {a.token_expires_at && (
                            <span className="text-xs text-muted-foreground">
                              token → {new Date(a.token_expires_at).toLocaleDateString()}
                            </span>
                          )}
                          {statusBadge(a.status)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </CardContent>
        </Card>

        <div className="space-y-6">
          {/* TikTok audit */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><ShieldCheck className="h-5 w-5" /> TikTok Direct Post audit</CardTitle>
              <CardDescription>Content Posting API — DIRECT_POST review state</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <Badge variant={auditMeta.tone}>{auditMeta.label}</Badge>
                {Boolean(audit?.reference) && <span className="text-muted-foreground">ref {String(audit?.reference)}</span>}
              </div>
              {Boolean(audit?.detail) && <p className="text-muted-foreground">{String(audit?.detail)}</p>}
              <p className="text-xs text-muted-foreground">
                Until approved, TikTok publishes fall back to MEDIA_UPLOAD (inbox draft).
              </p>
            </CardContent>
          </Card>

          {/* Publish queue */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><Layers className="h-5 w-5" /> Publish queue</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-3 text-center">
                <div>
                  <div className="text-2xl font-bold">{pendingCount}</div>
                  <div className="text-xs text-muted-foreground">pending</div>
                </div>
                <div>
                  <div className={`text-2xl font-bold ${failedCount ? 'text-red-500' : ''}`}>{failedCount}</div>
                  <div className="text-xs text-muted-foreground">failed</div>
                </div>
                <div>
                  <div className="text-2xl font-bold text-green-600">{queue.completed ?? 0}</div>
                  <div className="text-xs text-muted-foreground">completed</div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Media / browser */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><ImageIcon className="h-5 w-5" /> Media pipeline & browser</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex justify-between">
                <span>ComfyUI queue</span>
                <span>
                  {data?.media?.comfyui_queue?.running ?? 0} running · {data?.media?.comfyui_queue?.pending ?? 0} pending
                </span>
              </div>
              <div className="flex justify-between">
                <span>AI-generated assets</span>
                <span>{data?.media?.ai_generated_assets ?? 0}</span>
              </div>
              <div className="flex items-center justify-between">
                <span>Browser orchestrator</span>
                <span className="flex items-center gap-2">
                  {data?.browser_orchestrator.message ?? '—'}
                  {data?.browser_orchestrator.lock_held && (
                    <Button variant="outline" size="sm" onClick={releaseLock} disabled={releasing}>
                      {releasing ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Release'}
                    </Button>
                  )}
                </span>
              </div>
              <div className="flex gap-3 pt-1">
                <Link href="/mcp-stack" className="flex items-center gap-1 text-xs text-primary hover:underline">
                  MCP stack <ExternalLink className="h-3 w-3" />
                </Link>
                <Link href="/media" className="flex items-center gap-1 text-xs text-primary hover:underline">
                  Media library <ExternalLink className="h-3 w-3" />
                </Link>
                <Link href="/tiktok" className="flex items-center gap-1 text-xs text-primary hover:underline">
                  TikTok <ExternalLink className="h-3 w-3" />
                </Link>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
