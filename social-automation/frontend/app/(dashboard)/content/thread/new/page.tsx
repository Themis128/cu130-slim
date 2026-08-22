'use client'

import { useState, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { Plus, Trash2, Image as ImageIcon, Loader2, Save, Send, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useAccounts, useCreatePost, useUploadMedia } from '@/hooks/useQueries'
import { contentApi } from '@/services/api'
import type { SocialAccount } from '@/types'
import toast from 'react-hot-toast'

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

function cn(...c: (string | undefined | false)[]) { return c.filter(Boolean).join(' ') }

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
  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({})

  const charLimit = selectedPlatforms.includes('twitter') ? 280 : 500

  const togglePlatform = (p: Platform) => {
    if (!connectedPlatforms.includes(p)) { toast.error(`Connect your ${PLATFORM_NAMES[p]} account first`); return }
    setSelectedPlatforms(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])
  }

  const updateText = (id: string, text: string) => {
    setPosts(prev => prev.map(p => p.id === id ? { ...p, text } : p))
  }

  const addPost = () => {
    setPosts(prev => [...prev, { id: crypto.randomUUID(), text: '', mediaIds: [], mediaFiles: [] }])
  }

  const removePost = (id: string) => {
    if (posts.length <= 2) { toast.error('A thread needs at least 2 posts'); return }
    setPosts(prev => prev.filter(p => p.id !== id))
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
    if (overLimit) { toast.error(`Post exceeds ${charLimit} character limit`); return }

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
        const postData = post as unknown as { data?: { id?: string } }
        const postId = postData?.data?.id
        if (postId) {
          await contentApi.publishNow(postId)
        }
        toast.success('Thread published!')
      } else {
        toast.success('Thread saved as draft')
      }
      router.push('/content')
    } catch { toast.error('Failed to save thread') }
    finally { setPublishing(false) }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">New Thread</h1>
          <p className="text-muted-foreground text-sm">Connected posts published in sequence</p>
        </div>
      </div>

      {/* Platform selector */}
      <Card>
        <CardHeader><CardTitle className="text-sm">Platform</CardTitle></CardHeader>
        <CardContent>
          <div className="flex gap-3 flex-wrap">
            {THREAD_PLATFORMS.map(p => {
              const connected = connectedPlatforms.includes(p)
              const selected = selectedPlatforms.includes(p)
              return (
                <button
                  key={p}
                  onClick={() => togglePlatform(p)}
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
          {selectedPlatforms.includes('twitter') && selectedPlatforms.includes('threads') && (
            <p className="text-xs text-amber-500 mt-2">Twitter limit (280 chars) applies when both are selected</p>
          )}
        </CardContent>
      </Card>

      {/* Thread posts */}
      <div className="space-y-3">
        {posts.map((post, i) => {
          const isOver = post.text.length > charLimit
          return (
            <div key={post.id} className="relative flex gap-3">
              {/* Thread connector line */}
              <div className="flex flex-col items-center pt-4">
                <div className="w-8 h-8 rounded-full border-2 border-muted flex items-center justify-center text-xs font-bold text-muted-foreground flex-shrink-0">
                  {i + 1}
                </div>
                {i < posts.length - 1 && (
                  <div className="w-0.5 flex-1 mt-1 bg-border min-h-[1rem]" />
                )}
              </div>

              <Card className="flex-1">
                <CardContent className="pt-4 space-y-2">
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
                        onClick={() => fileInputRefs.current[post.id]?.click()}
                        disabled={post.mediaIds.length >= 4}
                      >
                        <ImageIcon className="h-3.5 w-3.5 mr-1" />
                        {post.mediaFiles.length > 0 ? `${post.mediaFiles.length} image(s)` : 'Add image'}
                      </Button>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={cn('text-xs', isOver ? 'text-destructive font-medium' : 'text-muted-foreground')}>
                        {post.text.length}/{charLimit}
                      </span>
                      {posts.length > 2 && (
                        <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-destructive" onClick={() => removePost(post.id)}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      )}
                    </div>
                  </div>
                  {/* Media preview */}
                  {post.mediaFiles.length > 0 && (
                    <div className="flex gap-2 flex-wrap">
                      {post.mediaFiles.map((f, fi) => (
                        <div key={fi} className="h-16 w-16 rounded border overflow-hidden">
                          <img src={URL.createObjectURL(f)} alt="" className="h-full w-full object-cover" />
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          )
        })}

        <div className="flex justify-center pl-11">
          <Button variant="outline" size="sm" onClick={addPost}>
            <Plus className="h-4 w-4 mr-2" />
            Add post to thread
          </Button>
        </div>
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
  )
}
