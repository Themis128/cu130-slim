'use client'

import { useState } from 'react'
import {
  Activity, Zap, AlertTriangle, RefreshCw, TrendingUp, Clock,
  CheckCircle, XCircle, DollarSign, ArrowLeft,
} from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Skeleton } from '@/components/ui/Skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { useAIProviderUsage, useAIProviders, useResetCircuitBreaker } from '@/hooks/useQueries'
import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/lib/utils'

const PLATFORM_COLOR: Record<string, string> = {
  cloudflare: '#f6821f',
  groq: '#f55036',
  gemini: '#4285f4',
  mistral: '#ff7000',
  cohere: '#39594d',
  openrouter: '#5b6b7c',
  nvidia: '#76b900',
  ollama: '#000000',
  huggingface: '#ffd21e',
  together: '#0f6fff',
}

export default function AIUsagePage() {
  const [days, setDays] = useState(7)
  const { data: usage, isLoading } = useAIProviderUsage(days)
  const { data: providers } = useAIProviders()
  const resetCircuit = useResetCircuitBreaker()

  const quotaPct = usage?.quota_pct
  const quotaWarning = quotaPct != null && quotaPct >= 80

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div className="flex items-center gap-3">
          <Link href="/settings/ai-providers">
            <Button variant="ghost" size="sm" className="gap-1.5">
              <ArrowLeft className="h-4 w-4" />
              Providers
            </Button>
          </Link>
          <div>
            <h1 className="text-3xl font-bold tracking-tight">AI Usage</h1>
            <p className="text-muted-foreground mt-1">Provider calls, latency, cost, and circuit breaker status</p>
          </div>
        </div>
        <Select value={days.toString()} onValueChange={(v) => setDays(Number(v))}>
          <SelectTrigger className="w-[140px]">
            <SelectValue placeholder="Time range" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="1">Last 24h</SelectItem>
            <SelectItem value="7">Last 7 days</SelectItem>
            <SelectItem value="30">Last 30 days</SelectItem>
            <SelectItem value="90">Last 90 days</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* KPI Cards */}
      {isLoading ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem' }}>
          {[1, 2, 3, 4].map((i) => (
            <Card key={i}>
              <CardContent className="p-6">
                <Skeleton className="h-4 w-24 mb-2" />
                <Skeleton className="h-8 w-16" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem' }}>
          <Card>
            <CardContent className="p-5">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs font-medium text-muted-foreground">Total Calls</p>
                  <p className="text-2xl font-bold mt-1 tabular-nums">
                    {(usage?.summary || []).reduce((acc, s) => acc + s.total_calls, 0).toLocaleString()}
                  </p>
                </div>
                <div className="p-2 rounded-full bg-blue-500/10">
                  <Activity className="h-4 w-4 text-blue-500" />
                </div>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs font-medium text-muted-foreground">Daily Neurons</p>
                  <p className="text-2xl font-bold mt-1 tabular-nums">
                    {(usage?.daily_neurons || 0).toLocaleString()}
                  </p>
                  {usage?.daily_neuron_budget != null && (
                    <p className="text-xs text-muted-foreground mt-1">
                      / {usage.daily_neuron_budget.toLocaleString()} budget
                    </p>
                  )}
                </div>
                <div className="p-2 rounded-full bg-orange-500/10">
                  <Zap className="h-4 w-4 text-orange-500" />
                </div>
              </div>
              {quotaWarning && (
                <div className="mt-3 flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                  <AlertTriangle className="h-3 w-3" />
                  <span>{quotaPct}% of daily budget used</span>
                </div>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs font-medium text-muted-foreground">Est. Cost</p>
                  <p className="text-2xl font-bold mt-1 tabular-nums">
                    ${(usage?.summary || []).reduce((acc, s) => acc + s.estimated_cost, 0).toFixed(4)}
                  </p>
                </div>
                <div className="p-2 rounded-full bg-green-500/10">
                  <DollarSign className="h-4 w-4 text-green-500" />
                </div>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs font-medium text-muted-foreground">Avg Latency</p>
                  <p className="text-2xl font-bold mt-1 tabular-nums">
                    {(() => {
                      const s = usage?.summary || []
                      if (!s.length) return '0'
                      const total = s.reduce((acc, x) => acc + x.avg_latency_ms * x.total_calls, 0)
                      const calls = s.reduce((acc, x) => acc + x.total_calls, 0)
                      return calls > 0 ? (total / calls).toFixed(0) : '0'
                    })()}ms
                  </p>
                </div>
                <div className="p-2 rounded-full bg-purple-500/10">
                  <Clock className="h-4 w-4 text-purple-500" />
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Quota progress bar */}
      {quotaPct != null && (
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium">Daily Cloudflare Neuron Quota</span>
              <span className={cn('text-sm font-bold tabular-nums', quotaWarning && 'text-amber-600 dark:text-amber-400')}>
                {quotaPct}%
              </span>
            </div>
            <div className="h-2 rounded-full bg-muted overflow-hidden">
              <div
                className={cn('h-full rounded-full transition-all', quotaWarning ? 'bg-amber-500' : 'bg-green-500')}
                style={{ width: `${Math.min(100, quotaPct)}%` }}
              />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Usage Table */}
      <Card>
        <CardHeader>
          <CardTitle>Usage by Provider / Model</CardTitle>
          <CardDescription>Calls, success rate, latency, and cost broken down by model</CardDescription>
        </CardHeader>
        <CardContent>
          {!usage?.summary?.length ? (
            <p className="text-sm text-muted-foreground py-8 text-center">
              No AI usage recorded yet. Generate content to see usage data here.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Provider</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead className="text-right">Calls</TableHead>
                    <TableHead className="text-right">Success</TableHead>
                    <TableHead className="text-right">Failed</TableHead>
                    <TableHead className="text-right">Avg Latency</TableHead>
                    <TableHead className="text-right">Neurons</TableHead>
                    <TableHead className="text-right">Cost</TableHead>
                    <TableHead>Last Call</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {usage.summary.map((row, i) => {
                    const successRate = row.total_calls > 0
                      ? ((row.successful_calls / row.total_calls) * 100).toFixed(1)
                      : '0'
                    return (
                      <TableRow key={`${row.provider}-${row.model}-${i}`}>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <div
                              className="w-6 h-6 rounded-full flex items-center justify-center text-white text-xs font-bold shrink-0"
                              style={{ backgroundColor: PLATFORM_COLOR[row.provider] ?? '#6366f1' }}
                            >
                              {row.provider[0].toUpperCase()}
                            </div>
                            <span className="font-medium capitalize">{row.provider}</span>
                          </div>
                        </TableCell>
                        <TableCell className="text-xs font-mono text-muted-foreground">{row.model}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{row.total_calls.toLocaleString()}</TableCell>
                        <TableCell className="text-right">
                          <span className="inline-flex items-center gap-1 text-sm">
                            <CheckCircle className="h-3.5 w-3.5 text-green-500" />
                            {row.successful_calls.toLocaleString()}
                          </span>
                        </TableCell>
                        <TableCell className="text-right">
                          {row.failed_calls > 0 ? (
                            <span className="inline-flex items-center gap-1 text-sm text-red-500">
                              <XCircle className="h-3.5 w-3.5" />
                              {row.failed_calls}
                            </span>
                          ) : (
                            <span className="text-sm text-muted-foreground">0</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">{row.avg_latency_ms.toFixed(0)}ms</TableCell>
                        <TableCell className="text-right font-mono text-sm">
                          {row.total_neurons > 0 ? row.total_neurons.toLocaleString() : '—'}
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">
                          {row.estimated_cost > 0 ? `$${row.estimated_cost.toFixed(4)}` : '$0'}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {row.last_call_at ? formatRelativeTime(row.last_call_at) : '—'}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Circuit Breaker Status */}
      <Card>
        <CardHeader>
          <CardTitle>Circuit Breaker Status</CardTitle>
          <CardDescription>Per-provider failure tracking and cooldown state</CardDescription>
        </CardHeader>
        <CardContent>
          {!providers?.length ? (
            <p className="text-sm text-muted-foreground py-8 text-center">No providers configured.</p>
          ) : (
            <div className="space-y-2">
              {providers.map((p) => {
                const isOpen = p.circuit_open
                const hasFailures = p.failure_count > 0
                return (
                  <div
                    key={p.name}
                    className="flex items-center justify-between p-3 rounded-lg border"
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className="w-7 h-7 rounded-full flex items-center justify-center text-white text-xs font-bold shrink-0"
                        style={{ backgroundColor: PLATFORM_COLOR[p.name] ?? '#6366f1' }}
                      >
                        {p.name[0].toUpperCase()}
                      </div>
                      <div>
                        <p className="font-medium capitalize text-sm">{p.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {isOpen
                            ? `Circuit open — cooldown until ${p.cooldown_until ? formatRelativeTime(p.cooldown_until) : 'soon'}`
                            : hasFailures
                              ? `${p.failure_count} recent failure${p.failure_count > 1 ? 's' : ''}`
                              : 'Healthy'}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {isOpen ? (
                        <Badge variant="destructive">Open</Badge>
                      ) : hasFailures ? (
                        <Badge variant="secondary">{p.failure_count} failures</Badge>
                      ) : (
                        <Badge variant="outline" className="text-green-600">
                          <CheckCircle className="h-3 w-3 mr-1" />
                          OK
                        </Badge>
                      )}
                      {(isOpen || hasFailures) && (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={resetCircuit.isPending}
                          onClick={() => resetCircuit.mutate(p.name)}
                        >
                          <RefreshCw className="h-3.5 w-3.5 mr-1" />
                          Reset
                        </Button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
