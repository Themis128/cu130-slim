'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowLeft, Download, Scissors, ZoomIn, Eraser, Crop, FileImage,
  Sparkles, Gauge, Type, Loader2, CheckCircle2, AlertCircle
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { mediaEnhanceApi, mediaApi } from '@/services/api'
import toast from 'react-hot-toast'

interface QualityScore {
  overall: number
  sharpness: number
  brightness: number
  contrast: number
  blur_detected: boolean
  too_dark: boolean
  too_bright: boolean
  issues: string[]
}

interface ImageInfo {
  width: number
  height: number
  mode: string
  format: string
}

interface Presets {
  presets: Record<string, { width: number; height: number; label: string }>
}

type Operation = 'resize' | 'upscale' | 'remove_bg' | 'smart_crop' | 'convert' | 'compress' | 'watermark' | 'alt_text'

export default function EnhancePage() {
  const params = useParams()
  const assetId = params.id as string

  const [asset, setAsset] = useState<any>(null)
  const [info, setInfo] = useState<ImageInfo | null>(null)
  const [quality, setQuality] = useState<QualityScore | null>(null)
  const [presets, setPresets] = useState<Presets['presets']>({})
  const [resultUrl, setResultUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState<Operation | null>(null)
  const [selectedPreset, setSelectedPreset] = useState('instagram_square')
  const [targetWidth, setTargetWidth] = useState(1080)
  const [targetHeight, setTargetHeight] = useState(1080)
  const [convertFormat, setConvertFormat] = useState('webp')
  const [watermarkText, setWatermarkText] = useState('')
  const [altText, setAltText] = useState<string | null>(null)

  useEffect(() => {
    mediaApi.getAsset(assetId).then(res => setAsset(res.data)).catch(() => {})
    mediaEnhanceApi.getPresets().then(res => setPresets(res.data.presets)).catch(() => {})
    mediaEnhanceApi.getInfo(assetId).then(res => setInfo(res.data)).catch(() => {})
    mediaEnhanceApi.getQuality(assetId).then(res => setQuality(res.data)).catch(() => {})
  }, [assetId])

  const handleOperation = useCallback(async (op: Operation, fn: () => Promise<any>) => {
    setLoading(op)
    setResultUrl(null)
    try {
      const res = await fn()
      if (res instanceof Blob) {
        const url = URL.createObjectURL(res)
        setResultUrl(url)
        toast.success('Enhancement complete')
      } else if (res.data?.alt_text) {
        setAltText(res.data.alt_text)
        toast.success('Alt text generated')
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `Failed: ${op}`)
    } finally {
      setLoading(null)
    }
  }, [])

  const downloadResult = () => {
    if (!resultUrl) return
    const a = document.createElement('a')
    a.href = resultUrl
    a.download = `enhanced_${asset?.filename || 'image'}`
    a.click()
  }

  const mediaUrl = asset?.storage_path
    ? `${process.env.NEXT_PUBLIC_API_URL || '/api/v1'}/media/view?path=${encodeURIComponent(asset.storage_path)}`
    : ''

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link href="/media">
          <Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button>
        </Link>
        <h1 className="text-2xl font-bold">AI Enhancement Studio</h1>
        {asset && <Badge variant="secondary">{asset.filename}</Badge>}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Preview */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Preview</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {/* Original */}
                <div>
                  <Label className="mb-2 block">Original</Label>
                  <div className="rounded-lg border bg-muted/30 p-4 flex items-center justify-center min-h-[200px]">
                    {mediaUrl && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={mediaUrl} alt="Original" className="max-h-[300px] object-contain rounded" />
                    )}
                  </div>
                </div>
                {/* Result */}
                {resultUrl && (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <Label>Result</Label>
                      <Button size="sm" variant="outline" onClick={downloadResult}>
                        <Download className="mr-2 h-4 w-4" /> Download
                      </Button>
                    </div>
                    <div className="rounded-lg border bg-muted/30 p-4 flex items-center justify-center min-h-[200px]">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={resultUrl} alt="Enhanced" className="max-h-[300px] object-contain rounded" />
                    </div>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Quality Score */}
          {quality && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Gauge className="h-5 w-5" /> Quality Score
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center gap-4">
                  <div className="text-4xl font-bold">{quality.overall}</div>
                  <div className="flex-1">
                    <ScoreBar label="Sharpness" value={quality.sharpness} />
                    <ScoreBar label="Brightness" value={quality.brightness} />
                    <ScoreBar label="Contrast" value={quality.contrast} />
                  </div>
                </div>
                {quality.issues.length > 0 && (
                  <div className="space-y-1">
                    {quality.issues.map((issue, i) => (
                      <div key={i} className="flex items-start gap-2 text-sm text-amber-600">
                        <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                        <span>{issue}</span>
                      </div>
                    ))}
                  </div>
                )}
                {quality.issues.length === 0 && (
                  <div className="flex items-center gap-2 text-sm text-green-600">
                    <CheckCircle2 className="h-4 w-4" /> No quality issues detected
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Image Info */}
          {info && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <FileImage className="h-5 w-5" /> Image Info
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div><span className="text-muted-foreground">Dimensions:</span> {info.width} × {info.height}</div>
                  <div><span className="text-muted-foreground">Format:</span> {info.format}</div>
                  <div><span className="text-muted-foreground">Mode:</span> {info.mode}</div>
                  <div><span className="text-muted-foreground">Aspect:</span> {(info.width / info.height).toFixed(2)}</div>
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Right: Operations */}
        <div className="space-y-4">
          {/* Platform Resize */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Crop className="h-5 w-5" /> Platform Resize
              </CardTitle>
              <CardDescription>Resize for specific social media platforms</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Select value={selectedPreset} onValueChange={setSelectedPreset}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(presets).map(([key, p]) => (
                    <SelectItem key={key} value={key}>{p.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                className="w-full"
                onClick={() => handleOperation('resize', () => mediaEnhanceApi.resize(assetId, { preset: selectedPreset }))}
                disabled={loading !== null}
              >
                {loading === 'resize' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Crop className="mr-2 h-4 w-4" />}
                Resize for {presets[selectedPreset]?.label || 'platform'}
              </Button>
            </CardContent>
          </Card>

          {/* AI Background Removal */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Eraser className="h-5 w-5" /> AI Background Removal
              </CardTitle>
              <CardDescription>Remove background using Cloudflare Workers AI</CardDescription>
            </CardHeader>
            <CardContent>
              <Button
                className="w-full"
                onClick={() => handleOperation('remove_bg', () => mediaEnhanceApi.removeBackground(assetId))}
                disabled={loading !== null}
              >
                {loading === 'remove_bg' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Eraser className="mr-2 h-4 w-4" />}
                Remove Background
              </Button>
            </CardContent>
          </Card>

          {/* AI Upscale */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ZoomIn className="h-5 w-5" /> AI Upscale
              </CardTitle>
              <CardDescription>Enlarge image 2x or 4x with sharpening</CardDescription>
            </CardHeader>
            <CardContent className="flex gap-2">
              <Button
                className="flex-1"
                onClick={() => handleOperation('upscale', () => mediaEnhanceApi.upscale(assetId, { scale: 2 }))}
                disabled={loading !== null}
              >
                {loading === 'upscale' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ZoomIn className="mr-2 h-4 w-4" />}
                2x
              </Button>
              <Button
                className="flex-1"
                onClick={() => handleOperation('upscale', () => mediaEnhanceApi.upscale(assetId, { scale: 4 }))}
                disabled={loading !== null}
              >
                {loading === 'upscale' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ZoomIn className="mr-2 h-4 w-4" />}
                4x
              </Button>
            </CardContent>
          </Card>

          {/* Smart Crop */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Scissors className="h-5 w-5" /> AI Smart Crop
              </CardTitle>
              <CardDescription>Crop to target dimensions, focusing on the main subject</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label>Width</Label>
                  <Input type="number" value={targetWidth} onChange={(e) => setTargetWidth(parseInt(e.target.value) || 0)} />
                </div>
                <div className="space-y-1">
                  <Label>Height</Label>
                  <Input type="number" value={targetHeight} onChange={(e) => setTargetHeight(parseInt(e.target.value) || 0)} />
                </div>
              </div>
              <Button
                className="w-full"
                onClick={() => handleOperation('smart_crop', () => mediaEnhanceApi.smartCrop(assetId, { target_width: targetWidth, target_height: targetHeight }))}
                disabled={loading !== null}
              >
                {loading === 'smart_crop' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Scissors className="mr-2 h-4 w-4" />}
                Smart Crop
              </Button>
            </CardContent>
          </Card>

          {/* Format Conversion */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileImage className="h-5 w-5" /> Format Conversion
              </CardTitle>
              <CardDescription>Convert between JPEG, PNG, WebP, AVIF</CardDescription>
            </CardHeader>
            <CardContent className="flex gap-2">
              {['webp', 'jpeg', 'png', 'avif'].map(fmt => (
                <Button
                  key={fmt}
                  variant={convertFormat === fmt ? 'default' : 'outline'}
                  className="flex-1"
                  onClick={() => {
                    setConvertFormat(fmt)
                    handleOperation('convert', () => mediaEnhanceApi.convert(assetId, { format: fmt }))
                  }}
                  disabled={loading !== null}
                >
                  {fmt.toUpperCase()}
                </Button>
              ))}
            </CardContent>
          </Card>

          {/* Watermark */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Type className="h-5 w-5" /> Watermark
              </CardTitle>
              <CardDescription>Add text watermark to image</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Input
                value={watermarkText}
                onChange={(e) => setWatermarkText(e.target.value)}
                placeholder="© Your Brand"
              />
              <Button
                className="w-full"
                onClick={() => handleOperation('watermark', () => mediaEnhanceApi.watermark(assetId, { text: watermarkText }))}
                disabled={loading !== null || !watermarkText}
              >
                {loading === 'watermark' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Type className="mr-2 h-4 w-4" />}
                Add Watermark
              </Button>
            </CardContent>
          </Card>

          {/* AI Alt Text */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Sparkles className="h-5 w-5" /> AI Alt Text
              </CardTitle>
              <CardDescription>Generate accessibility-focused alt text (WCAG)</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Button
                className="w-full"
                onClick={() => handleOperation('alt_text', () => mediaEnhanceApi.generateAltText(assetId))}
                disabled={loading !== null}
              >
                {loading === 'alt_text' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                Generate Alt Text
              </Button>
              {altText && (
                <div className="rounded-lg border bg-muted/30 p-3 text-sm">
                  <Label className="mb-1 block">Generated Alt Text:</Label>
                  <p className="text-foreground">{altText}</p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  const color = value >= 70 ? 'bg-green-500' : value >= 40 ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-2 text-xs mb-1">
      <span className="w-20 text-muted-foreground">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${value}%` }} />
      </div>
      <span className="w-8 text-right font-medium">{value}</span>
    </div>
  )
}
