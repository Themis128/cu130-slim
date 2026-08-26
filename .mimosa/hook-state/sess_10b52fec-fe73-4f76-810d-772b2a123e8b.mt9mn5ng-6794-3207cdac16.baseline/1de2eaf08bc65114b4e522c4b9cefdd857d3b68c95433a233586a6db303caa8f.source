'use client'

import { useState, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  Plus, Trash2, Image as ImageIcon, Loader2, Save, Send, ArrowLeft,
  GripVertical, Sparkles, Heart, MessageCircle, Repeat2, Share2, MoreHorizontal,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useAccounts, useCreatePost, useUploadMedia } from '@/hooks/useQueries'
import { contentApi, aiApi } from '@/services/api'
import type { SocialAccount } from '@/types'
import toast from 'react-hot-toast'
import { cn } from '@/lib/utils'

type Platform = SocialAccount['platform']
const THREAD_PLATFORMS: Platform[] = ['twitter', 'threads']
const PLATFORM_LIMITS: Record<string, number> = { twitter: 280, threads: 500 }
const PLATFORM_ICONS: Record<string, string> = { twitter: '𝕏', threads: '@' }
const PLATFORM_COLORS: Record<string, string> = { twitter: 'bg-sky-500', threads: 'bg-gray-800' }
const PLATFORM_NAMES: Record<string, string> = { twitter: 'Twitter/X', threads: 'Threads' }

interface ThreadPost {
  id: string
  text: string
  mediaIds: string[]
  mediaFiles: File[]
}

// ── Live Twitter thread preview ──────────────────────────────────────────────

function ThreadPreview({ posts, platform }: { posts: ThreadPost[]; platform: Platform | null }) {
  const filled = posts.filter(p => p.text.trim())
  if (!filled.length || !platform) {
    return (
      <div className="rounded-xl border border-dashed bg-muted/20 p-8 text-center">
        <p className="text-sm text-muted-foreground">Select a platform and start typing to see your thread preview</p>
      </div>
    )
  }

  if (platform === 'twitter') {
    return (
      <div className="space-y-0">
        {filled.map((post, i) => (
          <div key={post.id} className="flex gap-3 text-[13px] bg-white dark:bg-zinc-900 border-x border-t last:border-b rounded-none first:rounded-t-xl last:rounded-b-xl px-4 py-3">
            <div className="flex flex-col items-center gap-0">
              <div className="w-9 h-9 rounded-full bg-sky-100 dark:bg-sky-900 flex items-center justify-center text-[10px] font-bold text-sky-500 flex-shrink-0">You</div>
              {i < filled.length - 1 && <div className="w-0.5 flex-1 mt-1 bg-zinc-200 dark:bg-zinc-700 min-h-[24px]" />}
            </div>
            <div className="flex-1 min-w-0 pb-1">
              <div className="flex items-center gap-1 flex-wrap mb-1">
                <span className="font-bold text-zinc-900 dark:text-zinc-100 text-[12px]">Your Name</span>
                <span className="text-zinc-400 text-[11px]">@yourhandle · just now</span>
                <MoreHorizontal className="ml-auto h-3.5 w-3.5 text-zinc-400" />
              </div>
              <p className="whitespace-pre-wrap text-zinc-800 dark:text-zinc-200 leading-relaxed">{post.text}</p>
              {post.mediaFiles.length > 0 && (
                <div className="mt-2 grid grid-cols-2 gap-0.5 rounded-xl overflow-hidden">
                  {post.mediaFiles.slice(0, 4).map((f, fi) => (
                    <img key={fi} src={URL.createObjectURL(f)} alt="" className="w-full aspect-video object-cover" />
                  ))}
                </div>
              )}
              <div className="mt-2 flex items-center gap-4 text-zinc-400">
                <button className="hover:text-sky-500 transition-colors"><MessageCircle className="h-3.5 w-3.5" /></button>
                <button className="hover:text-green-500 transition-colors"><Repeat2 className="h-3.5 w-3.5" /></button>
                <button className="hover:text-red-500 transition-colors"><Heart className="h-3.5 w-3.5" /></button>
                <button className="hover:text-sky-500 ml-auto transition-colors"><Share2 className="h-3.5 w-3.5" /></button>
              </div>
            </div>
          </div>
        ))}
      </div>
    )
  }

  // Threads preview
  return (
    <div className="space-y-0">
      {filled.map((post, i) => (
        <div key={post.id} className="flex gap-3 text-[13px] bg-white dark:bg-zinc-900 border-x border-t last:border-b rounded-none first:rounded-t-xl last:rounded-b-xl px-4 py-3">
          <div className="flex flex-col items-center">
            <div className="w-9 h-9 rounded-full bg-zinc-200 dark:bg-zinc-700 flex items-center justify-center text-[10px] font-bold text-zinc-600 dark:text-zinc-300 flex-shrink-0">You</div>
            {i < filled.length - 1 && <div className="w-0.5 flex-1 mt-1 bg-zinc-200 dark:bg-zinc-700 min-h-[24px]" />}
          </div>
          <div className="flex-1 min-w-0 pb-1">
            <div className="flex items-center justify-between mb-1">
              <span className="font-semibold text-zinc-900 dark:text-zinc-100 text-[12px]">yourhandle</span>
              <div className="flex items-center gap-2 text-zinc-400">
                <span className="text-[11px]">just now</span>
                <MoreHorizontal className="h-3.5 w-3.5" />
              </div>
            </div>
            <p className="whitespace-pre-wrap text-zinc-800 dark:text-zinc-200 leading-relaxed">{post.text}</p>
            {post.mediaFiles.length > 0 && (
              <img src={URL.createObjectURL(post.mediaFiles[0])} alt="" className="mt-2 w-full rounded-xl object-cover max-h-40" />
            )}
            <div className="mt-2 flex gap-4 text-zinc-400">
              <button className="hover:text-red-500 transition-colors"><Heart className="h-3.5 w-3.5" /></button>
              <button className="hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"><MessageCircle className="h-3.5 w-3.5" /></button>
              <button className="hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"><Repeat2 className="h-3.5 w-3.5" /></button>
              <button className="hover:text-zinc-900 dark:hover:text-zinc-100 ml-auto transition-colors"><Share2 className="h-3.5 w-3.5" /></button>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Character ring (reused from new post page) ───────────────────────────────

function CharRing({ count, max }: { count: number; max: number }) {
  const size = 24
  const r = (size - 3) / 2
  const circ = 2 * Math.PI * r
  const ratio = Math.min(count / max, 1)
  const pct = count / max
  const stroke = pct >= 1 ? '#ef4444' : pct >= 0.8 ? '#f59e0b' : '#22c55e'
  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeWidth={2.5} className="text-muted/30" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={stroke} strokeWidth={2.5}
          strokeDasharray={circ} strokeDashoffset={circ * (1 - ratio)} strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.2s ease' }} />
      </svg>
      {pct >= 0.8 && (
        <span className="absolute text-[8px] font-semibold" style={{ color: stroke, lineHeight: 1 }}>
          {pct >= 1 ? '!' : max - count}
        </span>
      )}
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function NewThreadPage() {
  const router = useRouter()
  const { data: accounts } = useAccounts()
  const createPostMutation = useCreatePost()
  const uploadMediaMutation = useUploadMedia()

  const connectedPlatforms = (accounts as SocialAccount[] | undefined)
    ?.map(a => a.platform).filter(p => THREAD_PLATFORMS.includes(p)) || []

  const [selectedPlatforms, setSelectedPlatforms] = useState<Platform[]>([])
  const [posts, setPosts] = useState<ThreadPost[]>([
    { id: crypto.randomUUID(), text: '', mediaIds: [], mediaFiles: [] },
    { id: crypto.randomUUID(), text: '', mediaIds: [], mediaFiles: [] },
  ])
  const [publishing, setPublishing] = useState(false)
  const [splitting, setSplitting] = useState(false)
  const [splitText, setSplitText] = useState('')
  const [showSplitPanel, setShowSplitPanel] = useState(false)

  // Drag state
  const dragIdx = useRef<number | null>(null)

  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({})

  const previewPlatform: Platform | null = selectedPlatforms[0] ?? null
  const charLimit = selectedPlatforms.includes('twitter') ? PLATFORM_LIMITS.twitter : PLATFORM_LIMITS.threads

  const togglePlatform = (p: Platform) => {
    if (!connectedPlatforms.includes(p)) { toast.error(`Connect your ${PLATFORM_NAMES[p]} account first`); return }
    setSelectedPlatforms(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])
  }

  const updateText = (id: string, text: string) =>
    setPosts(prev => prev.map(p => p.id === id ? { ...p, text } : p))

  const addPost = () =>
    setPosts(prev => [...prev, { id: crypto.randomUUID(), text: '', mediaIds: [], mediaFiles: [] }])

  const removePost = (id: string) => {
    if (posts.length <= 2) { toast.error('A thread needs at least 2 posts'); return }
    setPosts(prev => prev.filter(p => p.id !== id))
  }

  // Drag-to-reorder
  const onDragStart = (i: number) => { dragIdx.current = i }
  const onDragOver = useCallback((e: React.DragEvent, i: number) => {
    e.preventDefault()
    if (dragIdx.current === null || dragIdx.current === i) return
    setPosts(prev => {
      const next = [...prev]
      const [moved] = next.splice(dragIdx.current!, 1)
      next.splice(i, 0, moved)
      dragIdx.current = i
      return next
    })
  }, [])
  const onDragEnd = () => { dragIdx.current = null }

  // AI split
  const handleAISplit = async () => {
    if (!splitText.trim()) { toast.error('Paste some text first'); return }
    setSplitting(true)
    try {
      const result = await aiApi.generateContent({
        prompt: `Split this content into a Twitter/Threads thread. Each part must be under ${charLimit} characters. Return only the thread parts separated by "---SPLIT---". Do not add part numbers.\n\n${splitText}`,
        platform: previewPlatform || 'twitter',
        tone: 'professional',
        length: 'short',
        include_hashtags: false,
        include_emojis: false,
      })
      const raw: string = result.data?.content || ''
      const parts = raw.split(/---SPLIT---/g).map(s => s.trim()).filter(Boolean)
      if (parts.length < 2) {
        // Fallback: split at sentence boundaries fitting within limit
        const sentences = splitText.match(/[^.!?]+[.!?]*/g) || [splitText]
        const chunks: string[] = []
        let current = ''
        for (const s of sentences) {
          if ((current + s).length > charLimit) {
            if (current) chunks.push(current.trim())
            current = s
          } else {
            current += s
          }
        }
        if (current.trim()) chunks.push(current.trim())
        parts.splice(0, parts.length, ...chunks)
      }
      setPosts(parts.map(text => ({ id: crypto.randomUUID(), text, mediaIds: [], mediaFiles: [] })))
      setShowSplitPanel(false)
      setSplitText('')
      toast.success(`Split into ${parts.length} posts`)
    } catch {
      toast.error('AI split failed')
    } finally {
      setSplitting(false)
    }
  }

  // Manual split at char limit
  const handleManualSplit = () => {
    if (!splitText.trim()) { toast.error('Paste some text first'); return }
    const chunks: string[] = []
    let remaining = splitText
    while (remaining.length > charLimit) {
      let splitAt = remaining.lastIndexOf(' ', charLimit)
      if (splitAt < 0) splitAt = charLimit
      chunks.push(remaining.slice(0, splitAt).trim())
      remaining = remaining.slice(splitAt).trim()
    }
    if (remaining) chunks.push(remaining)
    if (chunks.length < 2) { toast.error('Text fits in a single post'); return }
    setPosts(chunks.map(text => ({ id: crypto.randomUUID(), text, mediaIds: [], mediaFiles: [] })))
    setShowSplitPanel(false)
    setSplitText('')
    toast.success(`Split into ${chunks.length} posts`)
  }

  const handleMediaUpload = async (postId: string, files: FileList) => {
    const file = files[0]
    if (!file) return
    try {
      const result = await uploadMediaMutation.mutateAsync({ file, alt_text: '', tags: 'thread' })
      const mediaId = (result as { data?: { id?: string } })?.data?.id
      if (mediaId) {
        setPosts(prev => prev.map(p =>
          p.id === postId
            ? { ...p, mediaIds: [...p.mediaIds, mediaId], mediaFiles: [...p.mediaFiles, file] }
            : p
        ))
      }
    } catch { toast.error('Failed to upload image') }
  }

  const handleSave = async (asDraft: boolean) => {
    const filledPosts = posts.filter(p => p.text.trim())
    if (filledPosts.length < 2) { toast.error('Add content to at least 2 posts'); return }
    if (selectedPlatforms.length === 0) { toast.error('Select at least one platform'); return }
    const overLimit = filledPosts.find(p => p.text.length > charLimit)
    if (overLimit) { toast.error(`A post exceeds the ${charLimit} character limit`); return }

    setPublishing(true)
    try {
      const connectedAccounts = (accounts as SocialAccount[] | undefined) || []
      const targets = connectedAccounts
        .filter(a => selectedPlatforms.includes(a.platform))
        .map(a => ({ social_account_id: a.id }))

      const post = await createPostMutation.mutateAsync({
        content_text: filledPosts.map((p, i) => `[${i + 1}/${filledPosts.length}] ${p.text}`).join('\n\n'),
        hashtags: [],
        media_ids: filledPosts.flatMap(p => p.mediaIds),
        targets,
        metadata: {
          post_type: 'thread',
          thread: {
            posts: filledPosts.map(p => ({ text: p.text, media_ids: p.mediaIds })),
            platforms: selectedPlatforms,
          },
        },
      })

      if (!asDraft) {
        const postId = (post as unknown as { data?: { id?: string } })?.data?.id
        if (postId) await contentApi.publishNow(postId)
        toast.success('Thread published!')
      } else {
        toast.success('Thread saved as draft')
      }
      router.push('/content')
    } catch { toast.error('Failed to save thread') }
    finally { setPublishing(false) }
  }

  const filledCount = posts.filter(p => p.text.trim()).length

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight">New Thread</h1>
          <p className="text-muted-foreground text-sm">Connected posts published in sequence · {filledCount} of {posts.length} written</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setShowSplitPanel(v => !v)} className="gap-1.5">
            <Sparkles className="h-3.5 w-3.5" />
            AI Split
          </Button>
        </div>
      </div>

      {/* AI split panel */}
      {showSplitPanel && (
        <Card className="border-dashed">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Sparkles className="h-3.5 w-3.5 text-primary" />
              Paste long text — split into thread posts
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              value={splitText}
              onChange={e => setSplitText(e.target.value)}
              placeholder="Paste your long-form content here and we'll split it into thread posts…"
              rows={5}
              className="text-sm"
            />
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={handleAISplit} disabled={splitting || !splitText.trim()} className="gap-1.5">
                {splitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                AI Split
              </Button>
              <Button size="sm" variant="outline" onClick={handleManualSplit} disabled={!splitText.trim()}>
                Split at {charLimit} chars
              </Button>
              <Button size="sm" variant="ghost" onClick={() => { setShowSplitPanel(false); setSplitText('') }}>
                Cancel
              </Button>
              <span className="text-xs text-muted-foreground ml-auto">{splitText.length} chars</span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Platform selector */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex gap-3 flex-wrap items-center">
            <span className="text-sm font-medium text-muted-foreground">Platform:</span>
            {THREAD_PLATFORMS.map(p => {
              const connected = connectedPlatforms.includes(p)
              const selected = selectedPlatforms.includes(p)
              return (
                <button
                  key={p}
                  onClick={() => togglePlatform(p)}
                  className={cn(
                    'flex items-center gap-2 rounded-lg border-2 px-3 py-1.5 text-sm font-medium transition-colors',
                    selected ? 'border-primary bg-primary/10 text-primary' : 'border-muted bg-muted/30 text-muted-foreground hover:border-primary/50',
                    !connected && 'opacity-40 cursor-not-allowed'
                  )}
                >
                  <span className={cn('w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-white', PLATFORM_COLORS[p])}>
                    {PLATFORM_ICONS[p]}
                  </span>
                  {PLATFORM_NAMES[p]}
                  {!connected && <Badge variant="outline" className="text-xs ml-1">Connect</Badge>}
                </button>
              )
            })}
            {selectedPlatforms.includes('twitter') && selectedPlatforms.includes('threads') && (
              <p className="text-xs text-amber-500 ml-2">Twitter's 280-char limit applies</p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Main: editor + preview */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-6 items-start">
        {/* Left: Thread posts */}
        <div className="space-y-3">
          {posts.map((post, i) => {
            const isOver = post.text.length > charLimit
            return (
              <div
                key={post.id}
                draggable
                onDragStart={() => onDragStart(i)}
                onDragOver={e => onDragOver(e, i)}
                onDragEnd={onDragEnd}
                className="relative flex gap-3 group"
              >
                {/* Left: number + connector */}
                <div className="flex flex-col items-center pt-3 flex-shrink-0">
                  <div className={cn(
                    'w-7 h-7 rounded-full border-2 flex items-center justify-center text-xs font-bold transition-colors',
                    post.text.trim() ? 'border-primary text-primary bg-primary/10' : 'border-muted text-muted-foreground'
                  )}>
                    {i + 1}
                  </div>
                  {i < posts.length - 1 && (
                    <div className="w-0.5 flex-1 mt-1 bg-border min-h-[16px]" />
                  )}
                </div>

                {/* Card */}
                <Card className="flex-1 transition-shadow group-[.dragging]:shadow-lg">
                  <CardContent className="pt-3 space-y-2">
                    <Textarea
                      value={post.text}
                      onChange={e => updateText(post.id, e.target.value)}
                      placeholder={i === 0 ? 'Start your thread here…' : 'Continue the thread…'}
                      rows={3}
                      className="resize-none"
                    />
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <input
                          type="file"
                          accept="image/*,video/*"
                          className="hidden"
                          ref={el => { fileInputRefs.current[post.id] = el }}
                          onChange={e => e.target.files && handleMediaUpload(post.id, e.target.files)}
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs gap-1"
                          onClick={() => fileInputRefs.current[post.id]?.click()}
                          disabled={post.mediaIds.length >= 4}
                        >
                          <ImageIcon className="h-3 w-3" />
                          {post.mediaFiles.length > 0 ? `${post.mediaFiles.length} image(s)` : 'Add image'}
                        </Button>
                      </div>
                      <div className="flex items-center gap-2">
                        <CharRing count={post.text.length} max={charLimit} />
                        <span className={cn('text-xs tabular-nums', isOver ? 'text-destructive font-semibold' : 'text-muted-foreground')}>
                          {post.text.length}/{charLimit}
                        </span>
                        {posts.length > 2 && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6 text-muted-foreground hover:text-destructive"
                            onClick={() => removePost(post.id)}
                          >
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        )}
                      </div>
                    </div>
                    {post.mediaFiles.length > 0 && (
                      <div className="flex gap-2 flex-wrap">
                        {post.mediaFiles.map((f, fi) => (
                          <div key={fi} className="h-14 w-14 rounded border overflow-hidden">
                            <img src={URL.createObjectURL(f)} alt="" className="h-full w-full object-cover" />
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>

                {/* Drag handle */}
                <div className="absolute right-[-28px] top-3 opacity-0 group-hover:opacity-100 transition-opacity cursor-grab active:cursor-grabbing text-muted-foreground">
                  <GripVertical className="h-5 w-5" />
                </div>
              </div>
            )
          })}

          <div className="flex justify-center pl-10">
            <Button variant="outline" size="sm" onClick={addPost} className="gap-1.5">
              <Plus className="h-4 w-4" />
              Add post
            </Button>
          </div>

          {/* Actions */}
          <div className="flex gap-3 justify-end border-t pt-4">
            <Button variant="outline" onClick={() => handleSave(true)} disabled={publishing}>
              <Save className="h-4 w-4 mr-2" />
              Save Draft
            </Button>
            <Button onClick={() => handleSave(false)} disabled={publishing}>
              {publishing ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Send className="h-4 w-4 mr-2" />}
              Publish Thread
            </Button>
          </div>
        </div>

        {/* Right: Live preview */}
        <div className="lg:sticky lg:top-20 space-y-3">
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide px-1">
            Thread Preview · {previewPlatform ? PLATFORM_NAMES[previewPlatform] : 'Select a platform'}
          </h2>
          <ThreadPreview posts={posts} platform={previewPlatform} />
        </div>
      </div>
    </div>
  )
}
