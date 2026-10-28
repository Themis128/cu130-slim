'use client'

import { useState, useRef, useEffect, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { Upload, Loader2, Save, Send, ArrowLeft, X, Type, Link as LinkIcon, MapPin } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useAccounts, useCreatePost, useUploadMedia } from '@/hooks/useQueries'
import { contentApi } from '@/services/api'
import { SafeImage } from '@/components/SafeImage'
import { SafeVideo } from '@/components/SafeVideo'
import { isSafeImageUrl, isSafeVideoUrl } from '@/lib/utils'
import type { SocialAccount } from '@/types'
import toast from 'react-hot-toast'

type Platform = SocialAccount['platform']
const STORY_PLATFORMS: Platform[] = ['instagram', 'facebook']
const PLATFORM_ICONS: Record<string, string> = { instagram: '📷', facebook: 'f' }
const PLATFORM_COLORS: Record<string, string> = { instagram: 'bg-pink-500', facebook: 'bg-blue-700' }
const PLATFORM_NAMES: Record<string, string> = { instagram: 'Instagram', facebook: 'Facebook' }

function cn(...c: (string | undefined | false)[]) { return c.filter(Boolean).join(' ') }

export default function NewStoryPage() {
  const router = useRouter()
  const { data: accounts } = useAccounts()
  const createPostMutation = useCreatePost()
  const uploadMediaMutation = useUploadMedia()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const connectedAccounts = ((accounts as SocialAccount[] | undefined) || []).filter(
    a => STORY_PLATFORMS.includes(a.platform)
  )
  const connectedPlatforms = [...new Set(connectedAccounts.map(a => a.platform))]

  const [selectedPlatforms, setSelectedPlatforms] = useState<Platform[]>([])

  useEffect(() => {
    if (connectedAccounts.length === 0 || selectedPlatforms.length > 0) return
    setSelectedPlatforms(connectedPlatforms)
  }, [connectedAccounts])
  const [mediaFile, setMediaFile] = useState<File | null>(null)
  const [mediaId, setMediaId] = useState<string | null>(null)
  const [mediaPreview, setMediaPreview] = useState<string | null>(null)
  const [overlayText, setOverlayText] = useState('')
  const [linkUrl, setLinkUrl] = useState('')
  const [location, setLocation] = useState('')
  const [uploading, setUploading] = useState(false)
  const [publishing, setPublishing] = useState(false)

  const isVideo = mediaFile?.type.startsWith('video/')

  const safeMediaPreview = useMemo(() => {
    if (!mediaPreview) return null
    return isSafeVideoUrl(mediaPreview) || isSafeImageUrl(mediaPreview) ? mediaPreview : null
  }, [mediaPreview])

  const togglePlatform = (p: Platform) => {
    if (!connectedPlatforms.includes(p)) { toast.error(`Connect your ${PLATFORM_NAMES[p]} account first`); return }
    setSelectedPlatforms(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])
  }

  const handleFileSelect = async (files: FileList) => {
    const file = files[0]
    if (!file) return
    if (!file.type.startsWith('image/') && !file.type.startsWith('video/')) {
      toast.error('Stories support images and videos only')
      return
    }
    if (file.size > 100 * 1024 * 1024) { toast.error('File must be under 100 MB'); return }

    setMediaFile(file)
    setMediaPreview(URL.createObjectURL(file))
    setUploading(true)
    try {
      const result = await uploadMediaMutation.mutateAsync({ file, alt_text: 'Story', tags: 'story' })
      const id = (result as { data?: { id?: string } })?.data?.id
      if (id) setMediaId(id)
      else toast.error('Upload failed — no ID returned')
    } catch { toast.error('Upload failed') }
    finally { setUploading(false) }
  }

  const clearMedia = () => {
    setMediaFile(null)
    setMediaId(null)
    if (mediaPreview) URL.revokeObjectURL(mediaPreview)
    setMediaPreview(null)
  }

  const handleSave = async (asDraft: boolean) => {
    if (!mediaId) { toast.error('Upload a photo or video first'); return }
    if (selectedPlatforms.length === 0) { toast.error('Select at least one platform'); return }

    setPublishing(true)
    try {
      const allAccounts = (accounts as SocialAccount[] | undefined) || []
      const seen = new Set<string>()
      const targets: Array<{ social_account_id: string }> = []
      for (const plat of selectedPlatforms) {
        if (seen.has(plat)) continue
        const acct = allAccounts.find(a => a.platform === plat)
        if (acct) { targets.push({ social_account_id: acct.id }); seen.add(plat) }
      }

      const post = await createPostMutation.mutateAsync({
        content_text: overlayText || undefined,
        media_ids: [mediaId],
        targets,
        metadata: {
          post_type: 'story',
          story: {
            media_type: isVideo ? 'video' : 'image',
            overlay_text: overlayText || undefined,
            link_url: linkUrl || undefined,
            location: location || undefined,
            platforms: selectedPlatforms,
          },
        },
      })

      if (!asDraft) {
        const postData = post as unknown as { data?: { id?: string } }
        const postId = postData?.data?.id
        if (postId) {
          await contentApi.publishNow(postId)
        }
        toast.success('Story published!')
      } else {
        toast.success('Story saved as draft')
      }
      router.push('/content')
    } catch { toast.error('Failed to save story') }
    finally { setPublishing(false) }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">New Story</h1>
          <p className="text-muted-foreground text-sm">Vertical photo or video · disappears after 24 hours</p>
        </div>
      </div>

      {/* Platform */}
      <Card>
        <CardHeader><CardTitle className="text-sm">Platform</CardTitle></CardHeader>
        <CardContent>
          <div className="flex gap-3 flex-wrap">
            {STORY_PLATFORMS.map(p => {
              const connected = connectedPlatforms.includes(p)
              const selected = selectedPlatforms.includes(p)
              return (
                <button key={p} onClick={() => togglePlatform(p)}
                  className={cn(
                    'flex items-center gap-2 rounded-lg border-2 px-4 py-2 text-sm font-medium transition-colors',
                    selected ? 'border-primary bg-primary/10 text-primary' : 'border-muted bg-muted/30 text-muted-foreground hover:border-primary/50',
                    !connected && 'opacity-40 cursor-not-allowed'
                  )}
                >
                  <span className={cn('w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white', PLATFORM_COLORS[p])}>
                    {PLATFORM_ICONS[p]}
                  </span>
                  {PLATFORM_NAMES[p]}
                  {!connected && <Badge variant="outline" className="text-xs ml-1">Connect</Badge>}
                </button>
              )
            })}
          </div>
        </CardContent>
      </Card>

      {/* Media upload */}
      <Card>
        <CardHeader><CardTitle className="text-sm">Media</CardTitle></CardHeader>
        <CardContent>
          <input
            type="file"
            accept="image/*,video/*"
            className="hidden"
            ref={fileInputRef}
            onChange={e => e.target.files && handleFileSelect(e.target.files)}
          />

          {!safeMediaPreview ? (
            <button
              onClick={() => fileInputRef.current?.click()}
              className="w-full border-2 border-dashed border-muted rounded-xl flex flex-col items-center justify-center gap-3 py-16 hover:border-primary/50 transition-colors"
            >
              <Upload className="h-10 w-10 text-muted-foreground" />
              <div className="text-center">
                <p className="text-sm font-medium">Upload photo or video</p>
                <p className="text-xs text-muted-foreground mt-1">9:16 ratio recommended · Images or videos up to 100 MB</p>
              </div>
            </button>
          ) : (
            <div className="relative">
              {/* Story preview — 9:16 aspect ratio */}
              <div className="relative mx-auto rounded-xl overflow-hidden bg-black" style={{ aspectRatio: '9/16', maxHeight: '480px' }}>
                {isVideo ? (
                  <SafeVideo src={safeMediaPreview} className="h-full w-full object-cover" muted loop autoPlay playsInline />
                ) : (
                  <SafeImage src={safeMediaPreview} alt="Story preview" className="h-full w-full object-cover" />
                )}
                {uploading && (
                  <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
                    <Loader2 className="h-8 w-8 text-white animate-spin" />
                  </div>
                )}
                {overlayText && (
                  <div className="absolute bottom-16 left-0 right-0 flex justify-center px-4">
                    <span className="bg-black/60 text-white text-sm font-medium px-3 py-1.5 rounded-lg text-center">{overlayText}</span>
                  </div>
                )}
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="absolute top-2 right-2 h-8 w-8 bg-black/50 text-white hover:bg-black/70"
                onClick={clearMedia}
              >
                <X className="h-4 w-4" />
              </Button>
              <p className="text-xs text-center text-muted-foreground mt-2">
                {uploading ? 'Uploading…' : mediaId ? 'Uploaded' : 'Processing…'}
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Story options */}
      <Card>
        <CardHeader><CardTitle className="text-sm">Story Options</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
              <Type className="h-3.5 w-3.5" />Text Overlay
            </label>
            <Input
              value={overlayText}
              onChange={e => setOverlayText(e.target.value)}
              placeholder="Add text over the story…"
              maxLength={150}
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
              <LinkIcon className="h-3.5 w-3.5" />Link (swipe up / link sticker)
            </label>
            <Input
              value={linkUrl}
              onChange={e => setLinkUrl(e.target.value)}
              placeholder="https://example.com"
              type="url"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
              <MapPin className="h-3.5 w-3.5" />Location Tag
            </label>
            <Input
              value={location}
              onChange={e => setLocation(e.target.value)}
              placeholder="Athens, Greece"
            />
          </div>
        </CardContent>
      </Card>

      {/* Actions */}
      <div className="flex gap-3 justify-end border-t pt-4">
        <Button variant="outline" onClick={() => handleSave(true)} disabled={publishing || uploading}>
          <Save className="h-4 w-4 mr-2" />Save Draft
        </Button>
        <Button onClick={() => handleSave(false)} disabled={publishing || uploading || !mediaId}>
          {publishing ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Send className="h-4 w-4 mr-2" />}
          Publish Story
        </Button>
      </div>
    </div>
  )
}
