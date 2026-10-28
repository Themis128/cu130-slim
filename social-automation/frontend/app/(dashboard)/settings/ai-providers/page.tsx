'use client'

import { useState, useMemo } from 'react'
import {
  Loader2, Plug, Trash2, TestTube, CheckCircle, XCircle,
  Eye, EyeOff, Search, ChevronDown, ChevronRight, Zap, BarChart3,
} from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Switch } from '@/components/ui/Switch'
import { useAIProviderCatalog, useAIProviders, useUpsertAIProvider, useDeleteAIProvider, useTestAIProvider, useAIProviderModels } from '@/hooks/useQueries'

function ModelBrowser({ onPick }: { onPick: (id: string) => void }) {
  const { data: models = [], isLoading, isError } = useAIProviderModels('cloudflare')
  const [search, setSearch] = useState('')
  const [taskFilter, setTaskFilter] = useState('all')

  const tasks = useMemo(
    () => Array.from(new Set(models.map(m => m.task).filter(Boolean) as string[])).sort(),
    [models],
  )
  const filtered = models.filter(m =>
    (taskFilter === 'all' || m.task === taskFilter) &&
    (!search ||
      m.id.toLowerCase().includes(search.toLowerCase()) ||
      (m.description || '').toLowerCase().includes(search.toLowerCase())),
  )

  if (isLoading) return (
    <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" /> Loading Workers AI catalog…
    </div>
  )
  if (isError) return (
    <p className="text-sm text-destructive py-2">Failed to load model catalog — check Cloudflare credentials.</p>
  )

  return (
    <div className="rounded-md border p-3 space-y-2">
      <div className="flex gap-2">
        <Input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search models…"
          className="h-8 text-xs"
        />
        <select
          value={taskFilter}
          onChange={e => setTaskFilter(e.target.value)}
          className="h-8 text-xs rounded-md border bg-background px-2 shrink-0"
        >
          <option value="all">All tasks</option>
          {tasks.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>
      <p className="text-xs text-muted-foreground">
        {filtered.length} of {models.length} models — click to select
      </p>
      <div className="max-h-56 overflow-y-auto divide-y divide-border rounded">
        {filtered.map(m => (
          <button
            key={m.id}
            type="button"
            className="w-full text-left px-2 py-1.5 hover:bg-muted transition-colors"
            onClick={() => onPick(m.id)}
          >
            <span className="text-xs font-medium font-mono">{m.id}</span>
            <span className="block text-[11px] text-muted-foreground truncate">
              {m.task || 'Unknown task'}{m.description ? ` — ${m.description}` : ''}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

const PROVIDER_ICONS: Record<string, string> = {
  ollama: '🦙',
  nvidia: '🟢',
  huggingface: '🤗',
  openai: '✨',
  groq: '⚡',
  together: '🔗',
  cloudflare: '☁️',
}

export default function AIProvidersPage() {
  const { data: catalog = [], isLoading: catalogLoading, isError: catalogError, refetch: refetchCatalog } = useAIProviderCatalog()
  const { data: saved = [], isLoading: savedLoading, isError: savedError, refetch: refetchSaved } = useAIProviders()
  const upsertMutation = useUpsertAIProvider()
  const deleteMutation = useDeleteAIProvider()
  const testMutation = useTestAIProvider()

  const [testingProvider, setTestingProvider] = useState<string | null>(null)
  const [browsingModels, setBrowsingModels] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const [forms, setForms] = useState<Record<string, {
    api_key: string; base_url: string; default_model: string
    is_enabled: boolean; is_default: boolean; showKey: boolean
  }>>({})

  const savedMap = useMemo(() => Object.fromEntries(saved.map(p => [p.name, p])), [saved])

  const getForm = (name: string) => {
    if (forms[name]) return forms[name]
    const savedProvider = savedMap[name]
    const catalogEntry = catalog.find(c => c.name === name)
    return {
      api_key: '',
      base_url: savedProvider?.base_url || catalogEntry?.base_url || '',
      default_model: savedProvider?.default_model || catalogEntry?.default_model || '',
      is_enabled: savedProvider?.is_enabled ?? false,
      is_default: savedProvider?.is_default ?? false,
      showKey: false,
    }
  }

  const setField = (name: string, field: string, value: unknown) =>
    setForms(prev => ({ ...prev, [name]: { ...getForm(name), [field]: value } }))

  const handleSave = async (name: string) => {
    const form = getForm(name)
    await upsertMutation.mutateAsync({
      name,
      data: {
        api_key: form.api_key || undefined,
        base_url: form.base_url,
        default_model: form.default_model,
        is_enabled: form.is_enabled,
        is_default: form.is_default,
      },
    })
    setForms(prev => ({ ...prev, [name]: { ...prev[name] || getForm(name), api_key: '' } }))
  }

  const toggleExpanded = (name: string) =>
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })

  if (catalogLoading || savedLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (catalogError || savedError) {
    return (
      <div className="max-w-4xl space-y-4">
        <h1 className="text-3xl font-bold tracking-tight">AI Providers</h1>
        <Card className="border-destructive/30">
          <CardContent className="flex flex-col items-center gap-4 py-12">
            <XCircle className="h-10 w-10 text-destructive" />
            <div className="text-center">
              <p className="font-medium">Failed to load AI providers</p>
              <p className="text-sm text-muted-foreground mt-1">Your session may have expired.</p>
            </div>
            <div className="flex gap-3">
              <Button variant="outline" onClick={() => { refetchCatalog(); refetchSaved() }}>Retry</Button>
              <Button onClick={() => window.location.href = '/login'}>Log in again</Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  // Sort: enabled first, then disabled
  const sorted = [...catalog].sort((a, b) => {
    const aEnabled = savedMap[a.name]?.is_enabled ? 1 : 0
    const bEnabled = savedMap[b.name]?.is_enabled ? 1 : 0
    return bEnabled - aEnabled
  })

  const enabledCount = saved.filter(p => p.is_enabled).length
  const defaultProvider = saved.find(p => p.is_default)

  return (
    <div className="max-w-4xl space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">AI Providers</h1>
          <p className="text-muted-foreground mt-1">
            Configure cloud inference APIs. Once saved, any AI feature routes to your chosen provider.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0 mt-1">
          <Badge variant={enabledCount > 0 ? 'success' : 'secondary'} className="text-xs gap-1.5">
            <Zap className="h-3 w-3" />
            {enabledCount} of {catalog.length} active
          </Badge>
          {defaultProvider && (
            <Badge variant="default" className="text-xs">
              Default: {defaultProvider.display_name ?? defaultProvider.name}
            </Badge>
          )}
          <Link href="/settings/ai-providers/usage">
            <Button variant="outline" size="sm" className="gap-1.5">
              <BarChart3 className="h-4 w-4" />
              Usage
            </Button>
          </Link>
        </div>
      </div>

      {/* Provider cards */}
      <div className="space-y-3">
        {sorted.map(entry => {
          const form = getForm(entry.name)
          const existing = savedMap[entry.name]
          const isOpen = expanded.has(entry.name)
          const isSaving = upsertMutation.isPending
          const isTesting = testingProvider === entry.name
          const isEnabled = existing?.is_enabled ?? false

          return (
            <Card
              key={entry.name}
              className={isEnabled ? 'border-primary/40' : 'border-muted'}
            >
              {/* Collapsed header — always visible */}
              <button
                type="button"
                className="w-full text-left"
                onClick={() => toggleExpanded(entry.name)}
              >
                <CardHeader className="pb-3 pt-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3 min-w-0">
                      {isOpen
                        ? <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />
                        : <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />}
                      <span className="text-xl shrink-0">{PROVIDER_ICONS[entry.name] || '🤖'}</span>
                      <div className="min-w-0">
                        <CardTitle className="text-sm font-semibold">{entry.display_name}</CardTitle>
                        <p className="text-xs text-muted-foreground truncate">{entry.description}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {existing?.is_default && <Badge variant="default" className="text-xs">Default</Badge>}
                      {isEnabled ? (
                        <Badge variant="success" className="text-xs flex items-center gap-1">
                          <CheckCircle className="h-3 w-3" />Enabled
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="text-xs">Disabled</Badge>
                      )}
                    </div>
                  </div>
                </CardHeader>
              </button>

              {/* Expanded form */}
              {isOpen && (
                <CardContent className="space-y-4 pt-0">
                  {/* API key */}
                  {entry.requires_key && (
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                        API Key{existing?.has_key && <span className="text-green-500 normal-case ml-1">(saved)</span>}
                      </label>
                      <div className="relative">
                        <Input
                          type={form.showKey ? 'text' : 'password'}
                          value={form.api_key}
                          onChange={e => setField(entry.name, 'api_key', e.target.value)}
                          placeholder={existing?.has_key ? '•••••••••••• (leave blank to keep existing)' : `Enter ${entry.display_name} API key`}
                          className="pr-10"
                        />
                        <button
                          type="button"
                          className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                          onClick={() => setField(entry.name, 'showKey', !form.showKey)}
                        >
                          {form.showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Base URL + Default Model */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Base URL</label>
                      <Input
                        value={form.base_url}
                        onChange={e => setField(entry.name, 'base_url', e.target.value)}
                        placeholder={entry.base_url || (entry.name === 'ollama' ? 'http://ollama:11434' : 'https://api.example.com/v1')}
                        className="text-xs"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Default Model</label>
                      <Input
                        value={form.default_model}
                        onChange={e => setField(entry.name, 'default_model', e.target.value)}
                        placeholder={entry.default_model}
                        list={`models-${entry.name}`}
                        className="text-xs"
                      />
                      <datalist id={`models-${entry.name}`}>
                        {entry.model_examples.map(m => <option key={m} value={m} />)}
                      </datalist>
                    </div>
                  </div>

                  {/* Cloudflare model browser */}
                  {entry.name === 'cloudflare' && (
                    <div className="space-y-2">
                      <Button
                        size="sm"
                        variant="outline"
                        type="button"
                        onClick={() => setBrowsingModels(prev => prev === 'cloudflare' ? null : 'cloudflare')}
                      >
                        <Search className="mr-2 h-3.5 w-3.5" />
                        {browsingModels === 'cloudflare' ? 'Hide model catalog' : 'Browse Workers AI models'}
                      </Button>
                      {browsingModels === 'cloudflare' && (
                        <ModelBrowser onPick={id => setField('cloudflare', 'default_model', id)} />
                      )}
                    </div>
                  )}

                  {/* Enabled + Default toggles */}
                  <div className="flex items-center gap-6 py-1">
                    <label className="flex items-center gap-2 cursor-pointer select-none">
                      <Switch
                        checked={form.is_enabled}
                        onCheckedChange={(v: boolean) => setField(entry.name, 'is_enabled', v)}
                      />
                      <span className="text-sm">Enabled</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer select-none">
                      <Switch
                        checked={form.is_default}
                        onCheckedChange={(v: boolean) => setField(entry.name, 'is_default', v)}
                      />
                      <span className="text-sm">Set as default</span>
                    </label>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 pt-1">
                    <Button
                      size="sm"
                      onClick={() => handleSave(entry.name)}
                      disabled={isSaving}
                    >
                      {isSaving
                        ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                        : <Plug className="mr-2 h-3.5 w-3.5" />}
                      Save
                    </Button>

                    {existing && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setTestingProvider(entry.name)
                          testMutation.mutate(entry.name, { onSettled: () => setTestingProvider(null) })
                        }}
                        disabled={isTesting}
                      >
                        {isTesting
                          ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                          : <TestTube className="mr-2 h-3.5 w-3.5" />}
                        Test
                      </Button>
                    )}

                    {existing && (
                      <div className="ml-auto">
                        {confirmDelete === entry.name ? (
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-destructive">Remove this provider?</span>
                            <Button
                              size="sm"
                              variant="destructive"
                              onClick={() => {
                                deleteMutation.mutate(entry.name)
                                setConfirmDelete(null)
                              }}
                              disabled={deleteMutation.isPending}
                            >
                              Remove
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setConfirmDelete(null)}
                            >
                              Cancel
                            </Button>
                          </div>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-destructive hover:text-destructive"
                            onClick={() => setConfirmDelete(entry.name)}
                          >
                            <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                            Remove
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                </CardContent>
              )}
            </Card>
          )
        })}
      </div>
    </div>
  )
}
