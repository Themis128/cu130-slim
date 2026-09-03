'use client'

import { useState, useEffect, useCallback } from 'react'
import {
  Server,
  Globe,
  Monitor,
  Loader2,
  CheckCircle2,
  XCircle,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Wrench,
  Camera,
  KeyRound,
  ExternalLink,
  Cpu,
  Database,
  Linkedin,
  Facebook,
  Instagram,
  Zap,
  Activity,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Separator } from '@/components/ui/Separator'
import toast from 'react-hot-toast'
import api from '@/services/api'

// ── Types ────────────────────────────────────────────────────────────────────

interface MCPTool {
  name: string
  description: string
}

interface Service {
  id: string
  name: string
  type: 'browser_sidecar' | 'mcp_server'
  url: string
  description: string
  capabilities: string[]
  online: boolean
  status_code?: number
  data?: Record<string, unknown>
  error?: string
  tools?: MCPTool[]
  session_id?: string
}

interface StackStatus {
  status: string
  total_services: number
  online_services: number
  services: Service[]
}

// ── Service icons ────────────────────────────────────────────────────────────

function ServiceIcon({ id, className }: { id: string; className?: string }) {
  const map: Record<string, React.ComponentType<{ className?: string }>> = {
    linkedin_sidecar: Linkedin,
    facebook_sidecar: Facebook,
    instagram_sidecar: Instagram,
    linkedin_mcp: Linkedin,
    airbyte_mcp: Database,
  }
  const Icon = map[id] || Server
  return <Icon className={className} />
}

// ── Service Card ─────────────────────────────────────────────────────────────

function ServiceCard({
  service,
  onScreenshot,
  onSessionCheck,
}: {
  service: Service
  onScreenshot: (id: string) => void
  onSessionCheck: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const isSidecar = service.type === 'browser_sidecar'

  return (
    <Card className={service.online ? '' : 'opacity-75'}>
      <CardHeader>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className={`rounded-lg p-2 ${service.online ? 'bg-primary/10' : 'bg-muted'}`}>
              <ServiceIcon id={service.id} className="h-6 w-6" />
            </div>
            <div>
              <CardTitle className="flex items-center gap-2 text-lg">
                {service.name}
                {service.online ? (
                  <CheckCircle2 className="h-4 w-4 text-green-500" />
                ) : (
                  <XCircle className="h-4 w-4 text-red-500" />
                )}
              </CardTitle>
              <CardDescription className="mt-1">{service.description}</CardDescription>
            </div>
          </div>
          <Badge variant={service.online ? 'default' : 'destructive'}>
            {service.online ? 'Online' : 'Offline'}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        {/* Type badge */}
        <div className="mb-3 flex items-center gap-2">
          <Badge variant="outline" className="text-xs">
            {isSidecar ? (
              <><Monitor className="mr-1 h-3 w-3" /> Browser Sidecar</>
            ) : (
              <><Cpu className="mr-1 h-3 w-3" /> MCP Server</>
            )}
          </Badge>
          {service.tools && service.tools.length > 0 && (
            <Badge variant="outline" className="text-xs">
              <Wrench className="mr-1 h-3 w-3" /> {service.tools.length} tools
            </Badge>
          )}
          {service.status_code && (
            <Badge variant="outline" className="text-xs">
              HTTP {service.status_code}
            </Badge>
          )}
        </div>

        {/* Capabilities */}
        <div className="mb-3">
          <p className="mb-1 text-xs font-semibold text-muted-foreground">Capabilities</p>
          <div className="flex flex-wrap gap-1.5">
            {service.capabilities.map((cap) => (
              <span
                key={cap}
                className="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground"
              >
                {cap}
              </span>
            ))}
          </div>
        </div>

        {/* Error display */}
        {service.error && (
          <div className="mb-3 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {service.error}
          </div>
        )}

        {/* Session data for sidecars */}
        {service.data && isSidecar && (
          <div className="mb-3 rounded-md bg-muted/50 p-2 text-xs">
            <pre className="overflow-x-auto">{JSON.stringify(service.data, null, 2)}</pre>
          </div>
        )}

        {/* Tools list for MCP servers */}
        {service.tools && service.tools.length > 0 && (
          <div className="mb-3">
            <button
              onClick={() => setExpanded(!expanded)}
              className="flex items-center gap-1 text-xs font-semibold text-muted-foreground hover:text-foreground"
            >
              {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              Available Tools ({service.tools.length})
            </button>
            {expanded && (
              <div className="mt-2 space-y-1.5">
                {service.tools.map((tool) => (
                  <div
                    key={tool.name}
                    className="rounded-md border border-border/50 bg-muted/30 p-2"
                  >
                    <p className="font-mono text-xs font-semibold text-primary">{tool.name}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
                      {tool.description}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Actions */}
        <Separator className="my-3" />
        <div className="flex flex-wrap gap-2">
          {isSidecar && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onScreenshot(service.id)}
                disabled={!service.online}
              >
                <Camera className="mr-1 h-3 w-3" /> Screenshot
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onSessionCheck(service.id)}
                disabled={!service.online}
              >
                <KeyRound className="mr-1 h-3 w-3" /> Check Session
              </Button>
            </>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => window.open(service.url, '_blank')}
          >
            <ExternalLink className="mr-1 h-3 w-3" /> Open URL
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function MCPStackPage() {
  const [stack, setStack] = useState<StackStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [screenshotUrl, setScreenshotUrl] = useState<string | null>(null)

  const fetchStack = useCallback(async () => {
    try {
      setRefreshing(true)
      const { data } = await api.get('/mcp/stack')
      setStack(data)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to load MCP stack'
      toast.error(msg)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    fetchStack()
  }, [fetchStack])

  const handleScreenshot = async (serviceId: string) => {
    const url = `${api.defaults.baseURL}/mcp/stack/${serviceId}/screenshot`
    setScreenshotUrl(url)
    toast.success('Screenshot loaded in new view')
  }

  const handleSessionCheck = async (serviceId: string) => {
    try {
      const { data } = await api.post(`/mcp/stack/${serviceId}/session`)
      if (data.status === 'ok') {
        const loggedIn = data.result?.logged_in
        toast.success(
          `${serviceId}: ${loggedIn ? 'Session active' : 'No active session'} — ${data.result?.url || ''}`
        )
      } else {
        toast.error(`${serviceId}: ${data.error}`)
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Session check failed'
      toast.error(msg)
    }
  }

  if (loading) {
    return (
      <div className="flex h-[60vh] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  const sidecars = stack?.services.filter((s) => s.type === 'browser_sidecar') || []
  const mcpServers = stack?.services.filter((s) => s.type === 'mcp_server') || []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <Server className="h-6 w-6 text-primary" />
            MCP & Sidecar Stack
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Browser automation sidecars and MCP servers for social platform operations
          </p>
        </div>
        <Button onClick={fetchStack} disabled={refreshing} variant="outline">
          {refreshing ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="mr-2 h-4 w-4" />
          )}
          Refresh
        </Button>
      </div>

      {/* Summary */}
      {stack && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Card>
            <CardContent className="flex items-center gap-3 p-4">
              <div className="rounded-lg bg-primary/10 p-2">
                <Activity className="h-5 w-5 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold">{stack.online_services}</p>
                <p className="text-xs text-muted-foreground">Online Services</p>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="flex items-center gap-3 p-4">
              <div className="rounded-lg bg-blue-500/10 p-2">
                <Monitor className="h-5 w-5 text-blue-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{sidecars.length}</p>
                <p className="text-xs text-muted-foreground">Browser Sidecars</p>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="flex items-center gap-3 p-4">
              <div className="rounded-lg bg-purple-500/10 p-2">
                <Cpu className="h-5 w-5 text-purple-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{mcpServers.length}</p>
                <p className="text-xs text-muted-foreground">MCP Servers</p>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Screenshot viewer */}
      {screenshotUrl && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Camera className="h-5 w-5" /> Sidecar Screenshot
              </CardTitle>
              <Button size="sm" variant="ghost" onClick={() => setScreenshotUrl(null)}>
                Close
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <img
              src={screenshotUrl}
              alt="Sidecar screenshot"
              className="w-full rounded-lg border"
            />
          </CardContent>
        </Card>
      )}

      {/* Browser Sidecars */}
      <div>
        <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold">
          <Monitor className="h-5 w-5" />
          Browser Sidecars
          <Badge variant="outline" className="text-xs">
            {sidecars.filter((s) => s.online).length}/{sidecars.length} online
          </Badge>
        </h2>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {sidecars.map((service) => (
            <ServiceCard
              key={service.id}
              service={service}
              onScreenshot={handleScreenshot}
              onSessionCheck={handleSessionCheck}
            />
          ))}
        </div>
      </div>

      {/* MCP Servers */}
      <div>
        <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold">
          <Cpu className="h-5 w-5" />
          MCP Servers
          <Badge variant="outline" className="text-xs">
            {mcpServers.filter((s) => s.online).length}/{mcpServers.length} online
          </Badge>
        </h2>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {mcpServers.map((service) => (
            <ServiceCard
              key={service.id}
              service={service}
              onScreenshot={handleScreenshot}
              onSessionCheck={handleSessionCheck}
            />
          ))}
        </div>
      </div>

      {/* Architecture diagram */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Zap className="h-5 w-5 text-primary" />
            Architecture Overview
          </CardTitle>
          <CardDescription>
            How the sidecars and MCP servers work together
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 dark:border-blue-900 dark:bg-blue-950">
              <p className="font-semibold text-blue-700 dark:text-blue-300">
                Browser Sidecars (Playwright)
              </p>
              <p className="mt-1 text-muted-foreground">
                Handle <strong>writes</strong> — profile editing, photo uploads, posting.
                Use authenticated browser sessions for operations that official APIs
                don&apos;t support (personal profile edits, personal feed posting).
              </p>
            </div>
            <div className="rounded-lg border border-purple-200 bg-purple-50 p-3 dark:border-purple-900 dark:bg-purple-950">
              <p className="font-semibold text-purple-700 dark:text-purple-300">
                MCP Servers (Model Context Protocol)
              </p>
              <p className="mt-1 text-muted-foreground">
                Handle <strong>reads and searches</strong> — profile scraping, people/job
                search, messaging, feed reading, data connector orchestration. Complementary
                to sidecars: where sidecars write, MCP servers read and search.
              </p>
            </div>
            <div className="rounded-lg border border-green-200 bg-green-50 p-3 dark:border-green-900 dark:bg-green-950">
              <p className="font-semibold text-green-700 dark:text-green-300">
                Combined Coverage
              </p>
              <ul className="mt-1 space-y-1 text-muted-foreground">
                <li>• <strong>LinkedIn</strong>: Sidecar for profile edits + posting; MCP for profile reads, search, messaging, feed</li>
                <li>• <strong>Facebook</strong>: Sidecar for profile/page management + posting; Airbyte MCP for Ads analytics</li>
                <li>• <strong>Instagram</strong>: Private API sidecar for posting + profile writes</li>
                <li>• <strong>Airbyte</strong>: 500+ data connectors (FB Marketing, Stripe, Postgres, etc.)</li>
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
