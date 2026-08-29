'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeft, Sparkles, Wand2, Image as ImageIcon, Loader2, Download, RefreshCw, Check, Zap } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { Input } from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Label } from '@/components/ui/Label'
import { useGenerateImage } from '@/hooks/useQueries'
import { aiApi } from '@/services/api'
import type { AxiosResponse } from 'axios'
import toast from 'react-hot-toast'
import Link from 'next/link'

const CF_TXT2IMG_MODEL = '@cf/black-forest-labs/flux-1-schnell'

const STYLE_PRESETS = [
  { id: 'photorealistic', label: 'Photorealistic', suffix: 'photorealistic, 8k, sharp details, natural lighting, DSLR quality' },
  { id: 'professional', label: 'Professional', suffix: 'professional corporate photography, clean background, studio lighting, LinkedIn post visual' },
  { id: 'cinematic', label: 'Cinematic', suffix: 'cinematic, dramatic lighting, film grain, wide angle, moody atmosphere, high contrast' },
  { id: 'illustration', label: 'Illustration', suffix: 'digital illustration, flat design, bold colours, clean lines, modern graphic design' },
  { id: 'abstract', label: 'Abstract', suffix: 'abstract art, geometric shapes, vibrant colours, minimalist, modern art print' },
  { id: 'dark', label: 'Dark Cinematic', suffix: 'dark aesthetic, deep shadows, neon accents, cyberpunk mood, dark background, dramatic' },
]

function mediaDisplayUrl(storagePath?: string | null) {
  if (!storagePath) return ''
  const base = process.env.NEXT_PUBLIC_API_URL || '/api/v1'
  return `${base}/media/view?path=${encodeURIComponent(storagePath)}`
}

interface GeneratedImage {
  image_base64?: string
  storage_path?: string
  asset_id?: string
  prompt: string
  style: string
}

export default function GenerateImagePage() {
  const router = useRouter()
  const generateMutation = useGenerateImage()

  const [rawIdea, setRawIdea] = useState('')
  const [enhancedPrompt, setEnhancedPrompt] = useState('')
  const [negativePrompt, setNegativePrompt] = useState('')
  const [selectedStyle, setSelectedStyle] = useState('photorealistic')
  const [steps, setSteps] = useState(4)
  const [enhancing, setEnhancing] = useState(false)
  const [autoConfiguring, setAutoConfiguring] = useState(false)
  const [history, setHistory] = useState<GeneratedImage[]>([])

  const activePrompt = enhancedPrompt || rawIdea

  const handleAutoConfigure = async () => {
    if (!rawIdea.trim()) { toast.error('Enter an idea first'); return }
    setAutoConfiguring(true)
    try {
      const res = await aiApi.autoConfigurePrompt(rawIdea.trim(), 'image') as AxiosResponse<{
        style?: string; steps?: number; enhanced_prompt?: string; negative_prompt?: string
      }>
      const data = res?.data
      if (data) {
        if (data.style && STYLE_PRESETS.find(s => s.id === data.style)) setSelectedStyle(data.style)
        if (data.steps) setSteps(data.steps as 4 | 6 | 8)
        if (data.enhanced_prompt) setEnhancedPrompt(data.enhanced_prompt)
        if (data.negative_prompt) setNegativePrompt(data.negative_prompt)
        toast.success('Settings auto-configured by AI')
      }
    } catch {
      toast.error('Auto-configure failed')
    } finally {
      setAutoConfiguring(false)
    }
  }

  const handleEnhance = async () => {
    if (!rawIdea.trim()) { toast.error('Enter an idea first'); return }
    setEnhancing(true)
    try {
      const res = await aiApi.enhanceImagePrompt(rawIdea.trim(), selectedStyle)
      const data = (res as { data?: { prompt?: string; negative_prompt?: string } })?.data
      if (data?.prompt) {
        setEnhancedPrompt(data.prompt)
        setNegativePrompt(data.negative_prompt || '')
        toast.success('Prompt enhanced by AI')
      }
    } catch {
      toast.error('Prompt enhancement failed')
    } finally {
      setEnhancing(false)
    }
  }

  const handleGenerate = async () => {
    if (!activePrompt.trim()) { toast.error('Enter a prompt or idea first'); return }
    const stylePreset = STYLE_PRESETS.find(s => s.id === selectedStyle)
    const finalPrompt = enhancedPrompt
      ? enhancedPrompt
      : `${rawIdea.trim()}. ${stylePreset?.suffix ?? ''}`

    try {
      const res = await generateMutation.mutateAsync({
        prompt: finalPrompt.trim(),
        options: {
          provider: 'cloudflare',
          model: CF_TXT2IMG_MODEL,
          steps,
          negative_prompt: negativePrompt,
        },
      })
      const data = (res as { data?: { image_base64?: string; storage_path?: string; asset_id?: string } })?.data
      if (data) {
        setHistory(prev => [{
          image_base64: data.image_base64,
          storage_path: data.storage_path,
          asset_id: data.asset_id,
          prompt: finalPrompt,
          style: selectedStyle,
        }, ...prev.slice(0, 7)])
      }
    } catch {
      toast.error('Image generation failed')
    }
  }

  const latest = history[0]

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="icon" asChild>
          <Link href="/media"><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <div>
          <h1 className="text-3xl font-bold tracking-tight">AI Image Generator</h1>
          <p className="text-muted-foreground mt-1">
            Cloudflare Workers AI · FLUX.1-schnell · Saved to Media Library
          </p>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
        {/* ── Left: Controls ── */}
        <div className="space-y-5">
          {/* Idea / rough prompt */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Wand2 className="h-4 w-4 text-primary" />
                Your Idea
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                value={rawIdea}
                onChange={e => { setRawIdea(e.target.value); setEnhancedPrompt('') }}
                placeholder="Describe what you want — as rough as you like. e.g. 'a team working on laptops in a modern office'"
                rows={3}
                className="text-sm"
              />
              <div className="flex gap-2">
                <Button
                  variant="default"
                  size="sm"
                  onClick={handleAutoConfigure}
                  disabled={autoConfiguring || enhancing || !rawIdea.trim()}
                  className="flex-1"
                  title="AI reads your prompt and auto-selects style, steps, and optimised prompt"
                >
                  {autoConfiguring
                    ? <><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Configuring…</>
                    : <><Zap className="mr-2 h-3.5 w-3.5" />Auto-configure</>}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleEnhance}
                  disabled={enhancing || autoConfiguring || !rawIdea.trim()}
                  className="flex-1"
                >
                  {enhancing
                    ? <><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Enhancing…</>
                    : <><Sparkles className="mr-2 h-3.5 w-3.5" />Enhance Prompt</>}
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Enhanced prompt (editable) */}
          {enhancedPrompt && (
            <Card className="border-primary/30 bg-primary/5">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-2">
                  <Sparkles className="h-3.5 w-3.5 text-primary" />
                  Enhanced Prompt
                  <Badge variant="outline" className="text-[10px]">AI-generated · editable</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <Textarea
                  value={enhancedPrompt}
                  onChange={e => setEnhancedPrompt(e.target.value)}
                  rows={4}
                  className="text-xs font-mono"
                />
                {negativePrompt && (
                  <div>
                    <Label className="text-xs text-muted-foreground">Negative prompt</Label>
                    <Input
                      value={negativePrompt}
                      onChange={e => setNegativePrompt(e.target.value)}
                      className="text-xs mt-1"
                    />
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Style presets */}
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Style</CardTitle></CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-2">
                {STYLE_PRESETS.map(s => (
                  <button
                    key={s.id}
                    onClick={() => { setSelectedStyle(s.id); setEnhancedPrompt('') }}
                    className={[
                      'text-xs font-medium px-2 py-2 rounded border transition-colors',
                      selectedStyle === s.id
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border hover:bg-accent',
                    ].join(' ')}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Steps */}
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Quality</CardTitle></CardHeader>
            <CardContent>
              <div className="flex gap-2">
                {[
                  { v: 4, label: 'Fast (4 steps)' },
                  { v: 6, label: 'Balanced (6 steps)' },
                  { v: 8, label: 'Best (8 steps)' },
                ].map(opt => (
                  <button
                    key={opt.v}
                    onClick={() => setSteps(opt.v)}
                    className={[
                      'flex-1 text-xs font-medium py-2 rounded border transition-colors',
                      steps === opt.v
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border hover:bg-accent',
                    ].join(' ')}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground mt-2">
                FLUX schnell max is 8 steps. More steps = sharper details.
              </p>
            </CardContent>
          </Card>

          <Button
            onClick={handleGenerate}
            disabled={generateMutation.isPending || !activePrompt.trim()}
            className="w-full"
            size="lg"
          >
            {generateMutation.isPending
              ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Generating…</>
              : <><ImageIcon className="mr-2 h-4 w-4" />Generate Image</>}
          </Button>
        </div>

        {/* ── Right: Preview + History ── */}
        <div className="space-y-4">
          {/* Latest result */}
          <Card className="overflow-hidden">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Preview</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {generateMutation.isPending ? (
                <div className="aspect-square bg-muted flex flex-col items-center justify-center gap-3 text-muted-foreground">
                  <Loader2 className="h-8 w-8 animate-spin text-primary" />
                  <p className="text-sm">Generating with FLUX…</p>
                </div>
              ) : latest ? (
                <div className="relative">
                  {latest.storage_path ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                      src={mediaDisplayUrl(latest.storage_path)}
                      alt="Generated"
                      className="w-full aspect-square object-cover"
                    />
                  ) : latest.image_base64 ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                      src={`data:image/png;base64,${latest.image_base64}`}
                      alt="Generated"
                      className="w-full aspect-square object-cover"
                    />
                  ) : null}
                  <div className="absolute bottom-0 inset-x-0 bg-black/60 px-3 py-2 flex items-center justify-between">
                    <p className="text-[10px] text-white truncate flex-1 mr-2">{latest.prompt.slice(0, 80)}</p>
                    <div className="flex gap-1 shrink-0">
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-7 w-7 text-white hover:text-white hover:bg-white/20"
                        onClick={handleGenerate}
                        disabled={generateMutation.isPending}
                        title="Regenerate"
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                      </Button>
                      {latest.storage_path && (
                        <Button
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7 text-white hover:text-white hover:bg-white/20"
                          onClick={() => router.push('/media')}
                          title="View in Media Library"
                        >
                          <Check className="h-3.5 w-3.5" />
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="aspect-square bg-muted/40 flex flex-col items-center justify-center gap-3 text-muted-foreground">
                  <ImageIcon className="h-12 w-12 opacity-20" />
                  <p className="text-sm">Your image will appear here</p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* History strip */}
          {history.length > 1 && (
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Recent Generations</CardTitle></CardHeader>
              <CardContent>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {history.slice(1).map((img, i) => (
                    <div
                      key={i}
                      className="flex-shrink-0 h-16 w-16 rounded overflow-hidden border cursor-pointer hover:opacity-80 transition-opacity"
                      onClick={() => setHistory(prev => {
                        const copy = [...prev]
                        const item = copy.splice(i + 1, 1)[0]
                        return [item, ...copy]
                      })}
                    >
                      {img.storage_path ? (
                        /* eslint-disable-next-line @next/next/no-img-element */
                        <img src={mediaDisplayUrl(img.storage_path)} alt="" className="h-full w-full object-cover" />
                      ) : img.image_base64 ? (
                        /* eslint-disable-next-line @next/next/no-img-element */
                        <img src={`data:image/png;base64,${img.image_base64}`} alt="" className="h-full w-full object-cover" />
                      ) : null}
                    </div>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground mt-2">
                  All images are saved to your <Link href="/media" className="underline">Media Library</Link> automatically.
                </p>
              </CardContent>
            </Card>
          )}

          {latest?.storage_path && (
            <Button variant="outline" className="w-full" asChild>
              <Link href="/media">
                <Download className="mr-2 h-4 w-4" />
                View All in Media Library
              </Link>
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
