'use client'

import { useState } from 'react'
import { Loader2, Plug, Zap, Trash2, TestTube, CheckCircle, XCircle, Eye, EyeOff } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useAIProviderCatalog, useAIProviders, useUpsertAIProvider, useDeleteAIProvider, useTestAIProvider } from '@/hooks/useQueries'

const PROVIDER_ICONS: Record<string, string> = {
  ollama: '🦙',
  nvidia: '🟢',
  huggingface: '🤗',
  openai: '✨',
  groq: '⚡',
  together: '🔗',
}

export default function AIProvidersPage() {
  const { data: catalog = [], isLoading: catalogLoading, isError: catalogError, refetch: refetchCatalog } = useAIProviderCatalog()
  const { data: saved = [], isLoading: savedLoading, isError: savedError, refetch: refetchSaved } = useAIProviders()
  const upsertMutation = useUpsertAIProvider()
  const deleteMutation = useDeleteAIProvider()
  const testMutation = useTestAIProvider()

  const isLoading = catalogLoading || savedLoading
  const isError = catalogError || savedError

  // Local state per provider form
  const [testingProvider, setTestingProvider] = useState<string | null>(null)

  const [forms, setForms] = useState<Record<string, {
    api_key: string; base_url: string; default_model: string; is_enabled: boolean; is_default: boolean; showKey: boolean
  }>>({})

  const getForm = (name: string) => {
    if (forms[name]) return forms[name]
    const savedProvider = saved.find(p => p.name === name)
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

  const setField = (name: string, field: string, value: unknown) => {
    setForms(prev => ({ ...prev, [name]: { ...getForm(name), [field]: value } }))
  }

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

  const savedMap = Object.fromEntries(saved.map(p => [p.name, p]))

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="max-w-4xl space-y-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">AI Providers</h1>
        </div>
        <Card className="border-destructive/30">
          <CardContent className="flex flex-col items-center gap-4 py-12">
            <XCircle className="h-10 w-10 text-destructive" />
            <div className="text-center">
              <p className="font-medium">Failed to load AI providers</p>
              <p className="text-sm text-muted-foreground mt-1">
                Your session may have expired. Try refreshing or logging in again.
              </p>
            </div>
            <div className="flex gap-3">
              <Button variant="outline" onClick={() => { refetchCatalog(); refetchSaved() }}>
                Retry
              </Button>
              <Button variant="default" onClick={() => window.location.href = '/login'}>
                Log in again
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">AI Providers</h1>
        <p className="text-muted-foreground mt-1">
          Configure cloud inference APIs. Once saved, any AI feature can route to your chosen provider.
        </p>
      </div>

      <div className="grid gap-4">
        {catalog.map(entry => {
          const form = getForm(entry.name)
          const existing = savedMap[entry.name]
          const isSaving = upsertMutation.isPending
          const isTesting = testingProvider === entry.name

          return (
            <Card key={entry.name} className={existing?.is_enabled ? 'border-primary/40' : ''}>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">{PROVIDER_ICONS[entry.name] || '🤖'}</span>
                    <div>
                      <CardTitle className="text-base">{entry.display_name}</CardTitle>
                      <p className="text-xs text-muted-foreground">{entry.description}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {existing?.is_default && <Badge variant="default" className="text-xs">Default</Badge>}
                    {existing?.is_enabled ? (
                      <Badge variant="success" className="text-xs flex items-center gap-1">
                        <CheckCircle className="h-3 w-3" />Enabled
                      </Badge>
                    ) : (
                      <Badge variant="secondary" className="text-xs">Disabled</Badge>
                    )}
                  </div>
                </div>
              </CardHeader>

              <CardContent className="space-y-3">
                {/* API key */}
                {entry.requires_key && (
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                      API Key {existing?.has_key && <span className="text-green-500 normal-case">(saved)</span>}
                    </label>
                    <div className="relative">
                      <Input
                        type={form.showKey ? 'text' : 'password'}
                        value={form.api_key}
                        onChange={e => setField(entry.name, 'api_key', e.target.value)}
                        placeholder={existing?.has_key ? '••••••••••••  (leave blank to keep existing)' : `Enter ${entry.display_name} API key`}
                        className="pr-10"
                      />
                      <button
                        type="button"
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground"
                        onClick={() => setField(entry.name, 'showKey', !form.showKey)}
                      >
                        {form.showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                  </div>
                )}

                {/* Base URL */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Base URL</label>
                    <Input
                      value={form.base_url}
                      onChange={e => setField(entry.name, 'base_url', e.target.value)}
                      placeholder={entry.base_url || 'http://localhost:11434'}
                      className="text-xs"
                    />
                  </div>
                  <div className="space-y-1">
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

                {/* Toggles */}
                <div className="flex items-center gap-4">
                  <label className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded"
                      checked={form.is_enabled}
                      onChange={e => setField(entry.name, 'is_enabled', e.target.checked)}
                    />
                    <span className="text-sm">Enabled</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded"
                      checked={form.is_default}
                      onChange={e => setField(entry.name, 'is_default', e.target.checked)}
                    />
                    <span className="text-sm">Set as default</span>
                  </label>
                </div>

                {/* Actions */}
                <div className="flex gap-2 pt-1">
                  <Button
                    size="sm"
                    onClick={() => handleSave(entry.name)}
                    disabled={isSaving}
                  >
                    {isSaving ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Plug className="mr-2 h-3.5 w-3.5" />}
                    Save
                  </Button>
                  {existing && (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setTestingProvider(entry.name)
                          testMutation.mutate(entry.name, { onSettled: () => setTestingProvider(null) })
                        }}
                        disabled={isTesting}
                      >
                        {isTesting ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <TestTube className="mr-2 h-3.5 w-3.5" />}
                        Test
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-destructive ml-auto"
                        onClick={() => deleteMutation.mutate(entry.name)}
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </>
                  )}
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
