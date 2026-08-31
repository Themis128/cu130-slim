'use client'

import { useState, useRef, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { Image as ImageIcon, Loader2, Save, Send, ArrowLeft, X, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useAccounts, useCreatePost, useUploadMedia, useGenerateContent } from '@/hooks/useQueries'
import { contentApi } from '@/services/api'
import { SafeImage } from '@/components/SafeImage'
import { isSafeImageUrl } from '@/lib/utils'
import type { SocialAccount } from '@/types'
import toast from 'react-hot-toast'
import { SpellCheckButton } from '@/components/content/SpellCheckButton'
import { SeoPanel } from '@/components/content/SeoPanel'

function cn(...c: (string | undefined | false)[]) { return c.filter(Boolean).join(' ') }

export default function NewArticlePage() {
  const router = useRouter()
  const { data: accounts } = useAccounts()
  const createPostMutation = useCreatePost()
  const uploadMediaMutation = useUploadMedia()
  const generateContentMutation = useGenerateContent()
  const coverInputRef = useRef<HTMLInputElement>(null)

  const linkedInAccounts = (accounts as SocialAccount[] | undefined)?.filter(a => a.platform === 'linkedin') || []
  const connectedLinkedIn = linkedInAccounts.find(a => a.account_type === 'organization' || a.meta_data?.account_type === 'organization')
    ?? linkedInAccounts[0]

  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [tags, setTags] = useState('')
  const [coverId, setCoverId] = useState<string | null>(null)
  const [coverPreview, setCoverPreview] = useState<string | null>(null)
  const [coverUploading, setCoverUploading] = useState(false)
  const [aiGenerating, setAiGenerating] = useState(false)
  const [publishing, setPublishing] = useState(false)

  const wordCount = body.trim() ? body.trim().split(/\s+/).length : 0
  const readTime = Math.max(1, Math.ceil(wordCount / 200))

  const safeCoverPreview = useMemo(() => {
    if (!coverPreview) return null
    return isSafeImageUrl(coverPreview) ? coverPreview : null
  }, [coverPreview])

  const handleCoverSelect = async (files: FileList) => {
    const file = files[0]
    if (!file || !file.type.startsWith('image/')) { toast.error('Cover must be an image'); return }
    setCoverPreview(URL.createObjectURL(file))
    setCoverUploading(true)
    try {
      const result = await uploadMediaMutation.mutateAsync({ file, alt_text: title || 'Article cover', tags: 'article,cover' })
      const id = (result as { data?: { id?: string } })?.data?.id
      if (id) setCoverId(id)
    } catch { toast.error('Cover upload failed') }
    finally { setCoverUploading(false) }
  }

  const clearCover = () => {
    setCoverId(null)
    if (coverPreview) URL.revokeObjectURL(coverPreview)
    setCoverPreview(null)
  }

  const handleAIExpand = async () => {
    if (!title.trim() && !body.trim()) { toast.error('Add a title or some content first'); return }
    setAiGenerating(true)
    try {
      const result = await generateContentMutation.mutateAsync({
        prompt: title.trim()
          ? `Write a professional LinkedIn article about: "${title}". ${body.trim() ? `Expand on this draft: ${body}` : 'Write 400-600 words with clear sections.'}`
          : `Expand this LinkedIn article draft: ${body}`,
        platform: 'linkedin',
        tone: 'professional',
        length: 'long',
        include_hashtags: false,
        include_emojis: false,
      })
      const generated = result.data?.content || ''
      if (generated) {
        setBody(generated)
        toast.success('Article expanded with AI')
      }
    } catch { toast.error('AI generation failed') }
    finally { setAiGenerating(false) }
  }

  const handleSave = async (asDraft: boolean) => {
    if (!title.trim()) { toast.error('Add an article title'); return }
    if (!body.trim()) { toast.error('Add article body content'); return }
    if (!connectedLinkedIn) { toast.error('Connect your LinkedIn account first'); return }

    setPublishing(true)
    try {
      const tagList = tags.split(',').map(t => t.trim()).filter(Boolean)

      const post = await createPostMutation.mutateAsync({
        content_text: body,
        hashtags: tagList,
        media_ids: coverId ? [coverId] : [],
        targets: [{ social_account_id: connectedLinkedIn.id }],
        metadata: {
          post_type: 'article',
          article: {
            title,
            body,
            cover_media_id: coverId || undefined,
            tags: tagList,
            word_count: wordCount,
            read_time_minutes: readTime,
          },
        },
      })

      if (!asDraft) {
        const postData = post as unknown as { data?: { id?: string } }
        const postId = postData?.data?.id
        if (postId) {
          await contentApi.publishNow(postId)
        }
        toast.success('Article published to LinkedIn!')
      } else {
        toast.success('Article saved as draft')
      }
      router.push('/content')
    } catch { toast.error('Failed to save article') }
    finally { setPublishing(false) }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">New Article</h1>
          <p className="text-muted-foreground text-sm">Long-form content for LinkedIn</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-600/10 px-3 py-1 text-xs font-medium text-blue-600">
            <span className="w-4 h-4 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-bold">in</span>
            LinkedIn
          </span>
          {!connectedLinkedIn && (
            <Badge variant="destructive" className="text-xs">Not connected</Badge>
          )}
        </div>
      </div>

      {/* Cover image */}
      <Card>
        <CardHeader><CardTitle className="text-sm">Cover Image</CardTitle></CardHeader>
        <CardContent>
          <input type="file" accept="image/*" className="hidden" ref={coverInputRef}
            onChange={e => e.target.files && handleCoverSelect(e.target.files)} />
          {!safeCoverPreview ? (
            <button onClick={() => coverInputRef.current?.click()}
              className="w-full border-2 border-dashed border-muted rounded-xl flex flex-col items-center justify-center gap-2 py-10 hover:border-primary/50 transition-colors">
              <ImageIcon className="h-8 w-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">Add a cover image (1200×627 recommended)</p>
            </button>
          ) : (
            <div className="relative rounded-xl overflow-hidden" style={{ aspectRatio: '1200/627' }}>
              <SafeImage src={safeCoverPreview} alt="Cover" className="w-full h-full object-cover" />
              {coverUploading && (
                <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
                  <Loader2 className="h-8 w-8 text-white animate-spin" />
                </div>
              )}
              <Button variant="ghost" size="icon" className="absolute top-2 right-2 h-8 w-8 bg-black/50 text-white hover:bg-black/70" onClick={clearCover}>
                <X className="h-4 w-4" />
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Article content */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm">Content</CardTitle>
          <Button variant="outline" size="sm" onClick={handleAIExpand} disabled={aiGenerating}>
            <Sparkles className="h-3.5 w-3.5 mr-1.5" />
            {aiGenerating ? 'Writing…' : 'AI Expand'}
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Title *</label>
            <Input
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="Your article headline"
              className="text-lg font-semibold h-12"
              maxLength={150}
            />
            <p className="text-xs text-muted-foreground">{title.length}/150</p>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Body *</label>
            <Textarea
              value={body}
              onChange={e => setBody(e.target.value)}
              placeholder="Write your article content here…"
              rows={16}
              className="resize-y min-h-[300px]"
            />
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{wordCount} words</span>
              <span>~{readTime} min read</span>
            </div>
            <SpellCheckButton text={body} onApply={setBody} />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Tags (comma separated)</label>
            <Input
              value={tags}
              onChange={e => setTags(e.target.value)}
              placeholder="technology, serverless, startups"
            />
          </div>
        </CardContent>
      </Card>

      {/* Article preview card */}
      {title.trim() && (
        <Card className="border-dashed">
          <CardHeader><CardTitle className="text-sm text-muted-foreground">LinkedIn Preview</CardTitle></CardHeader>
          <CardContent>
            <div className="rounded-lg border overflow-hidden">
              {safeCoverPreview && (
                <div style={{ aspectRatio: '1200/627' }} className="bg-muted overflow-hidden">
                  <SafeImage src={safeCoverPreview} alt="" className="w-full h-full object-cover" />
                </div>
              )}
              <div className="p-4 space-y-1">
                <p className="font-semibold text-sm leading-snug">{title}</p>
                {wordCount > 0 && <p className="text-xs text-muted-foreground">{wordCount} words · {readTime} min read</p>}
                {tags && (
                  <div className="flex flex-wrap gap-1 pt-1">
                    {tags.split(',').map(t => t.trim()).filter(Boolean).map(t => (
                      <Badge key={t} variant="secondary" className="text-xs">#{t}</Badge>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* SEO Analysis */}
      {body.trim().length > 20 && (
        <SeoPanel content={body} platform="linkedin" />
      )}

      {/* Actions */}
      <div className="flex gap-3 justify-end border-t pt-4">
        <Button variant="outline" onClick={() => handleSave(true)} disabled={publishing || coverUploading}>
          <Save className="h-4 w-4 mr-2" />Save Draft
        </Button>
        <Button onClick={() => handleSave(false)} disabled={publishing || coverUploading || !connectedLinkedIn}>
          {publishing ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Send className="h-4 w-4 mr-2" />}
          Publish to LinkedIn
        </Button>
      </div>
    </div>
  )
}
