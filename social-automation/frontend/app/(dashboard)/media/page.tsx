'use client'

import { useState, useRef, useEffect } from 'react'
import { Search, Image as ImageIcon, Upload, Trash2, Eye, Sparkles, FolderOpen, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger } from '@/components/ui/Dialog'
import { Skeleton } from '@/components/ui/Skeleton'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'
import { useMedia, useUploadMedia, useDeleteMedia, useGenerateImage } from '@/hooks/useQueries'
import { EmptyState } from '@/components/ui/EmptyState'
import { ImageViewerDialog } from '@/components/ui/ImageViewerDialog'
import { useUndoDelete } from '@/hooks/useUndoDelete'
import { aiApi } from '@/services/api'
import type { MediaAsset } from '@/types'
import toast from 'react-hot-toast'

/** Free-tier Cloudflare Workers AI txt2img (same as carousel pipeline). */
const CF_TXT2IMG_MODEL = '@cf/black-forest-labs/flux-1-schnell'
const CF_TXT2IMG_STEPS = 4

/** Universal display URL — backend re-encodes exotic formats to PNG on the fly. */
function mediaDisplayUrl(storagePath?: string | null) {
  if (!storagePath) return ''
  const base = process.env.NEXT_PUBLIC_API_URL || '/api/v1'
  return `${base}/media/view?path=${encodeURIComponent(storagePath)}`
}

function formatBytes(n?: number | null) {
  if (!n || n <= 0) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

interface PendingFile {
  file: File
  preview: string
  altText: string
  generating: boolean
}

const typeOptions = [
  { value: 'all', label: 'All Types' },
  { value: 'image', label: 'Images' },
  { value: 'video', label: 'Videos' },
  { value: 'generated', label: 'AI Generated' },
]

export default function MediaPage() {
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [generatePrompt, setGeneratePrompt] = useState('')
  const [generateOpen, setGenerateOpen] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([])
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [viewerItem, setViewerItem] = useState<MediaAsset | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const apiType = typeFilter === 'all' ? undefined : typeFilter
  const { data, isLoading } = useMedia({ type: apiType, page, page_size: 20 })
  const uploadMutation = useUploadMedia()
  const deleteMutation = useDeleteMedia()
  const generateMutation = useGenerateImage()

  const allMedia = data?.assets || data?.items || (Array.isArray(data) ? data : [])

  const { deleteWithUndo, pendingIds } = useUndoDelete<MediaAsset>(
    async (item) => { await deleteMutation.mutateAsync(item.id) }
  )

  const visibleMedia = allMedia.filter((a: MediaAsset) => !pendingIds.has(a.id))
  const media = search
    ? visibleMedia.filter((a: MediaAsset) =>
        a.filename?.toLowerCase().includes(search.toLowerCase()) ||
        a.alt_text?.toLowerCase().includes(search.toLowerCase()) ||
        a.generation_prompt?.toLowerCase().includes(search.toLowerCase())
      )
    : visibleMedia
  const totalPages = Math.max(
    1,
    Math.ceil((data?.total ?? media.length) / (data?.page_size || 20))
  )

  // Revoke object URLs when pending files change / unmount
  useEffect(() => {
    return () => {
      pendingFiles.forEach((pf) => {
        if (pf.preview) URL.revokeObjectURL(pf.preview)
      })
    }
  }, [pendingFiles])

  const generateAltText = async (file: File, index: number) => {
    setPendingFiles(prev => prev.map((f, i) => i === index ? { ...f, generating: true } : f))
    try {
      const baseName = file.name.replace(/\.[^.]+$/, '').replace(/[-_]/g, ' ')
      const res = await aiApi.generateContent({
        prompt: `Write a concise, descriptive image alt text (under 125 characters) for an image file named "${baseName}". Output only the alt text, no quotes or extra explanation.`,
        platform: 'linkedin',
        tone: 'professional',
        length: 'short',
        include_hashtags: false,
        include_emojis: false,
      })
      const text = (res as { data?: { content?: string } })?.data?.content
        || (res as { content?: string })?.content
        || ''
      const clean = text.replace(/^["']|["']$/g, '').trim().slice(0, 125)
      setPendingFiles(prev => prev.map((f, i) => i === index ? { ...f, altText: clean, generating: false } : f))
    } catch {
      setPendingFiles(prev => prev.map((f, i) => i === index ? { ...f, generating: false } : f))
    }
  }

  const handleFileSelect = async (files: FileList) => {
    const fileArray = Array.from(files)
    const initial: PendingFile[] = fileArray.map(file => ({
      file,
      preview: file.type.startsWith('image/') ? URL.createObjectURL(file) : '',
      altText: '',
      generating: false,
    }))
    setPendingFiles(initial)
    setUploadDialogOpen(true)
    for (let i = 0; i < fileArray.length; i++) {
      if (fileArray[i].type.startsWith('image/')) {
        generateAltText(fileArray[i], i)
      }
    }
  }

  const closeUploadDialog = () => {
    setPendingFiles((prev) => {
      prev.forEach((pf) => { if (pf.preview) URL.revokeObjectURL(pf.preview) })
      return []
    })
    setUploadDialogOpen(false)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleConfirmUpload = async () => {
    setUploading(true)
    let successCount = 0
    for (const pf of pendingFiles) {
      try {
        await uploadMutation.mutateAsync({
          file: pf.file,
          alt_text: pf.altText,
          tags: '',
          silent: true,
        })
        successCount++
      } catch {
        toast.error(`Failed to upload ${pf.file.name}`)
      }
    }
    setUploading(false)
    closeUploadDialog()
    if (successCount > 0) toast.success(`${successCount} file${successCount > 1 ? 's' : ''} uploaded`)
  }

  const handleGenerate = async () => {
    if (!generatePrompt.trim()) return
    try {
      await generateMutation.mutateAsync({
        prompt: generatePrompt.trim(),
        options: {
          provider: 'cloudflare',
          model: CF_TXT2IMG_MODEL,
          steps: CF_TXT2IMG_STEPS,
        },
      })
      setGeneratePrompt('')
      setGenerateOpen(false)
      setPage(1)
    } catch {
      toast.error('Failed to generate image')
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Media Library</h1>
          <p className="text-muted-foreground mt-1">Manage your images, videos, and AI-generated content</p>
        </div>
        <div className="flex gap-2">
          <Dialog open={generateOpen} onOpenChange={setGenerateOpen}>
            <DialogTrigger asChild>
              <Button variant="outline">
                <Sparkles className="mr-2 h-4 w-4" />
                AI Generate
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Generate Image with AI</DialogTitle>
              </DialogHeader>
              <div className="py-4 space-y-2">
                <Textarea
                  value={generatePrompt}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setGeneratePrompt(e.target.value)}
                  placeholder="Describe the image you need for a LinkedIn post…"
                  rows={4}
                />
                <p className="text-xs text-muted-foreground">
                  Uses Cloudflare Workers AI ({CF_TXT2IMG_MODEL.split('/').pop()}, {CF_TXT2IMG_STEPS} steps).
                  Stored at max 768px on the long edge — open an image to zoom.
                </p>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setGenerateOpen(false)}>Cancel</Button>
                <Button onClick={handleGenerate} disabled={generateMutation.isPending || !generatePrompt.trim()}>
                  {generateMutation.isPending ? (
                    <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Generating…</>
                  ) : (
                    'Generate'
                  )}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Button onClick={() => fileInputRef.current?.click()}>
            <Upload className="mr-2 h-4 w-4" />
            Upload
          </Button>
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp,image/avif,image/bmp,image/tiff,image/svg+xml,image/heic,image/heif,.tif,.tiff,.heic,.heif,.avif,video/*"
        multiple
        className="hidden"
        onChange={(e) => e.target.files && handleFileSelect(e.target.files)}
      />

      <Dialog
        open={uploadDialogOpen}
        onOpenChange={(open) => {
          if (!uploading) {
            if (!open) closeUploadDialog()
            else setUploadDialogOpen(true)
          }
        }}
      >
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              Review & Upload — AI Alt Text
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            {pendingFiles.map((pf, i) => (
              <div key={`${pf.file.name}-${i}`} className="flex gap-4 rounded-lg border p-3">
                {pf.preview ? (
                  <img src={pf.preview} alt="" className="h-20 w-20 rounded-md object-cover flex-shrink-0 border" />
                ) : (
                  <div className="h-20 w-20 rounded-md bg-muted flex items-center justify-center flex-shrink-0 border">
                    <ImageIcon className="h-7 w-7 text-muted-foreground" />
                  </div>
                )}
                <div className="flex-1 min-w-0 space-y-2">
                  <p className="text-sm font-medium truncate">{pf.file.name}</p>
                  <div>
                    <Label className="text-xs text-muted-foreground flex items-center gap-1 mb-1">
                      Alt text
                      {pf.generating && <Loader2 className="h-3 w-3 animate-spin text-primary" />}
                      {!pf.generating && pf.altText && (
                        <Badge variant="outline" className="text-[10px] px-1 py-0 ml-1">
                          <Sparkles className="h-2.5 w-2.5 mr-0.5" />AI
                        </Badge>
                      )}
                    </Label>
                    <Input
                      value={pf.altText}
                      onChange={e => setPendingFiles(prev => prev.map((f, idx) => idx === i ? { ...f, altText: e.target.value } : f))}
                      placeholder={pf.generating ? 'Generating alt text…' : 'Enter alt text (optional)'}
                      disabled={pf.generating}
                      maxLength={125}
                    />
                    <p className="text-[10px] text-muted-foreground mt-1 text-right">{pf.altText.length}/125</p>
                  </div>
                  {!pf.generating && !pf.altText && pf.file.type.startsWith('image/') && (
                    <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => generateAltText(pf.file, i)}>
                      <Sparkles className="mr-1 h-3 w-3" />
                      Generate alt text
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeUploadDialog} disabled={uploading}>
              Cancel
            </Button>
            <Button onClick={handleConfirmUpload} disabled={uploading || pendingFiles.some(f => f.generating)}>
              {uploading ? (
                <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Uploading…</>
              ) : (
                <><Upload className="mr-2 h-4 w-4" />Upload {pendingFiles.length} file{pendingFiles.length > 1 ? 's' : ''}</>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Filters */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-col sm:flex-row gap-4">
            <div className="relative flex-1 max-w-sm">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search media..."
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1) }}
                className="pl-10"
              />
            </div>
            <Select
              value={typeFilter}
              onValueChange={(v) => { setTypeFilter(v); setPage(1) }}
            >
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="Type" />
              </SelectTrigger>
              <SelectContent>
                {typeOptions.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {/* Media Grid */}
      <Card>
        <CardContent className="pt-0">
          {isLoading ? (
            <div className="p-8 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
              {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
                <Skeleton key={i} className="aspect-square rounded-lg" />
              ))}
            </div>
          ) : media.length === 0 ? (
            search ? (
              <EmptyState
                icon={FolderOpen}
                title={`No results for "${search}"`}
                description="Try a different search term, or clear the filter to browse all assets."
                primaryAction={{ label: 'Clear search', onClick: () => setSearch(''), variant: 'outline' }}
              />
            ) : (
              <EmptyState
                icon={ImageIcon}
                title="Your media library is empty"
                description="Upload images and videos to reuse across posts, or generate visuals with AI."
                primaryAction={{ label: 'Upload files', onClick: () => fileInputRef.current?.click(), icon: Upload }}
                secondaryAction={{ label: 'Generate with AI', onClick: () => setGenerateOpen(true), icon: Sparkles, variant: 'outline' }}
              />
            )
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4 p-4">
                {media.map((item: MediaAsset) => {
                  const isVideo = !!item.mime_type?.startsWith('video/')
                  const isAi = item.source === 'ai-generated' || item.source === 'comfyui'
                  return (
                  <div
                    key={item.id}
                    className="relative group aspect-square rounded-lg overflow-hidden border bg-muted/50 cursor-zoom-in"
                    onClick={() => setViewerItem(item)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter') setViewerItem(item) }}
                  >
                    {item.storage_path ? (
                      isVideo ? (
                        <>
                          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                          <video
                            src={mediaDisplayUrl(item.storage_path)}
                            muted
                            preload="metadata"
                            className="h-full w-full object-cover"
                          />
                          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                            <div className="rounded-full bg-black/60 p-2">
                              <Eye className="h-5 w-5 text-white" />
                            </div>
                          </div>
                        </>
                      ) : (
                        /* eslint-disable-next-line @next/next/no-img-element */
                        <img
                          src={mediaDisplayUrl(item.storage_path)}
                          alt={item.alt_text || item.filename || 'Media'}
                          loading="lazy"
                          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                          className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-105"
                        />
                      )
                    ) : (
                      <div className="flex h-full items-center justify-center text-muted-foreground">
                        <ImageIcon className="h-8 w-8" />
                      </div>
                    )}
                    <div className="absolute inset-x-0 bottom-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity px-2 py-1 pointer-events-none">
                      <p className="text-[10px] text-white truncate">{item.filename}</p>
                      {(item.width && item.height) || item.size_bytes ? (
                        <p className="text-[9px] text-white/80 truncate">
                          {[item.width && item.height ? `${item.width}×${item.height}` : null, formatBytes(item.size_bytes)]
                            .filter(Boolean)
                            .join(' · ')}
                        </p>
                      ) : null}
                    </div>
                    <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="bg-white/90 h-7 w-7"
                        title="View (zoom & pan)"
                        onClick={(e) => { e.stopPropagation(); setViewerItem(item) }}
                      >
                        <Eye className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="bg-white/90 h-7 w-7"
                        title="Delete"
                        onClick={(e) => { e.stopPropagation(); deleteWithUndo(item, item.filename || 'Media') }}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                    {isAi && (
                      <Badge className="absolute bottom-8 left-2" variant="outline">
                        <Sparkles className="mr-1 h-3 w-3" />
                        AI
                      </Badge>
                    )}
                  </div>
                  )
                })}
              </div>

              {totalPages > 1 && (
                <div className="flex items-center justify-between p-4 border-t">
                  <p className="text-sm text-muted-foreground">
                    Page {page} of {totalPages}
                    {data?.total != null ? ` • ${data.total} items` : ''}
                  </p>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage(p => Math.max(1, p - 1))}
                      disabled={page === 1}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                      disabled={page === totalPages}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <ImageViewerDialog
        open={!!viewerItem}
        onOpenChange={(o) => { if (!o) setViewerItem(null) }}
        item={
          viewerItem
            ? {
                src: mediaDisplayUrl(viewerItem.storage_path),
                alt: viewerItem.alt_text || viewerItem.filename || 'Media',
                filename: viewerItem.filename || undefined,
                mime_type: viewerItem.mime_type,
                width: viewerItem.width,
                height: viewerItem.height,
                size_bytes: viewerItem.size_bytes,
              }
            : null
        }
      />
    </div>
  )
}
