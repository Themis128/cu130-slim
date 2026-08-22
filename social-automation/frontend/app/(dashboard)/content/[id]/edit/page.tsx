'use client'

import { useState, useEffect, useRef } from 'react'
import { useRouter, useParams } from 'next/navigation'
import { ArrowLeft, Save, Send, Trash2, X, Image as ImageIcon, Calendar, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/Dialog'
import { Skeleton } from '@/components/ui/Skeleton'
import { usePost, useUpdatePost, useDeletePost, usePublishPost, useUploadMedia } from '@/hooks/useQueries'
import { contentApi } from '@/services/api'
import type { Post } from '@/types'
import Link from 'next/link'
import toast from 'react-hot-toast'

export default function EditPostPage() {
  const router = useRouter()
  const params = useParams()
  const id = (params?.id ?? '') as string

  const { data: post, isLoading } = usePost(id)
  const updateMutation = useUpdatePost()
  const deleteMutation = useDeletePost()
  const publishMutation = usePublishPost()
  const uploadMediaMutation = useUploadMedia()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [content, setContent] = useState('')
  const [hashtags, setHashtags] = useState<string[]>([])
  const [hashtagInput, setHashtagInput] = useState('')
  const [linkUrl, setLinkUrl] = useState('')
  const [mediaIds, setMediaIds] = useState<string[]>([])
  const [newMediaFiles, setNewMediaFiles] = useState<File[]>([])
  const [scheduleDate, setScheduleDate] = useState('')
  const [scheduleOpen, setScheduleOpen] = useState(false)

  useEffect(() => {
    if (post) {
      setContent((post as Post).content_text || '')
      setHashtags((post as Post).hashtags || [])
      setLinkUrl((post as Post).link_url || '')
      setMediaIds(((post as Post).media_ids || []).map(String))
      setScheduleDate((post as Post).scheduled_at ? new Date((post as Post).scheduled_at!).toISOString().slice(0, 16) : '')
    }
  }, [post])

  const handleSave = async () => {
    try {
      await updateMutation.mutateAsync({
        id,
        data: {
          content_text: content,
          hashtags,
          link_url: linkUrl || undefined,
        } as Partial<Post>,
      })
      router.push('/content')
    } catch {
      // mutation already toasts
    }
  }

  const handlePublish = async () => {
    await handleSave()
    try {
      await publishMutation.mutateAsync(id)
      router.push('/content')
    } catch {
      toast.error('Failed to publish')
    }
  }

  const handleSchedule = async () => {
    if (!scheduleDate) { toast.error('Pick a date and time'); return }
    try {
      await updateMutation.mutateAsync({
        id,
        data: { content_text: content, hashtags, scheduled_at: new Date(scheduleDate).toISOString() } as Partial<Post>,
      })
      await contentApi.schedulePost(id, new Date(scheduleDate).toISOString())
      setScheduleOpen(false)
      router.push('/content')
    } catch {
      toast.error('Failed to schedule')
    }
  }

  const handleDelete = async () => {
    if (!confirm('Delete this post? This cannot be undone.')) return
    try {
      await deleteMutation.mutateAsync(id)
      router.push('/content')
    } catch {
      // mutation already toasts
    }
  }

  const handleMediaUpload = async (files: FileList) => {
    for (const file of Array.from(files)) {
      try {
        const res = await uploadMediaMutation.mutateAsync({ file, alt_text: '', tags: '' })
        const newId = (res as { data?: { id?: string } })?.data?.id
        if (newId) setMediaIds(prev => [...prev, newId])
        setNewMediaFiles(prev => [...prev, file])
      } catch {
        toast.error(`Failed to upload ${file.name}`)
      }
    }
  }

  const addHashtag = () => {
    const tag = hashtagInput.trim().replace(/^#/, '')
    if (tag && !hashtags.includes(tag)) setHashtags(prev => [...prev, tag])
    setHashtagInput('')
  }

  if (isLoading) {
    return (
      <div className="max-w-3xl mx-auto space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  if (!post) {
    return (
      <div className="max-w-3xl mx-auto py-12 text-center">
        <p className="text-muted-foreground">Post not found.</p>
        <Button asChild className="mt-4"><Link href="/content">Back to Content</Link></Button>
      </div>
    )
  }

  const typedPost = post as Post

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" asChild>
            <Link href="/content"><ArrowLeft className="h-4 w-4" /></Link>
          </Button>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Edit Post</h1>
            <div className="flex items-center gap-2 mt-0.5">
              <Badge variant="outline" className="capitalize">{typedPost.status}</Badge>
              {typedPost.targets?.[0]?.social_account?.platform && (
                <Badge variant="secondary" className="capitalize">
                  {typedPost.targets[0].social_account.platform}
                </Badge>
              )}
            </div>
          </div>
        </div>
        <Button variant="destructive" size="sm" onClick={handleDelete} disabled={deleteMutation.isPending}>
          <Trash2 className="mr-2 h-4 w-4" />
          Delete
        </Button>
      </div>

      {/* Content */}
      <Card>
        <CardHeader><CardTitle>Content</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="What do you want to share?"
            className="min-h-[200px] font-mono text-base"
            rows={8}
          />
          <div>
            <label className="block text-sm font-medium mb-2">Link URL</label>
            <Input
              value={linkUrl}
              onChange={(e) => setLinkUrl(e.target.value)}
              placeholder="https://example.com"
              type="url"
            />
          </div>
        </CardContent>
      </Card>

      {/* Hashtags */}
      <Card>
        <CardHeader><CardTitle>Hashtags</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input
              value={hashtagInput}
              onChange={(e) => setHashtagInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addHashtag())}
              placeholder="Add hashtag (press Enter)"
            />
            <Button variant="outline" onClick={addHashtag}>Add</Button>
          </div>
          {hashtags.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {hashtags.map((tag) => (
                <Badge key={tag} variant="secondary" className="gap-1 pr-1">
                  #{tag}
                  <button
                    type="button"
                    onClick={() => setHashtags(prev => prev.filter(t => t !== tag))}
                    className="ml-1 rounded-full hover:bg-muted p-0.5"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Media */}
      <Card>
        <CardHeader><CardTitle>Media</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*,video/*"
            multiple
            className="hidden"
            onChange={(e) => e.target.files && handleMediaUpload(e.target.files)}
          />
          <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()} disabled={uploadMediaMutation.isPending}>
            {uploadMediaMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ImageIcon className="mr-2 h-4 w-4" />}
            Add Media
          </Button>
          {mediaIds.length > 0 && (
            <p className="text-sm text-muted-foreground">{mediaIds.length} media file(s) attached</p>
          )}
          {newMediaFiles.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {newMediaFiles.map((file, i) => (
                <div key={i} className="relative h-20 w-20 rounded-lg overflow-hidden border">
                  {file.type.startsWith('image/') && (
                    <img src={URL.createObjectURL(file)} alt={file.name} className="h-full w-full object-cover" />
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Actions */}
      <div className="flex flex-col sm:flex-row gap-3 justify-end border-t pt-6">
        <Button variant="outline" onClick={handleSave} disabled={updateMutation.isPending}>
          <Save className="mr-2 h-4 w-4" />
          {updateMutation.isPending ? 'Saving...' : 'Save Draft'}
        </Button>
        {typedPost.status !== 'published' && (
          <>
            <Button variant="outline" onClick={() => setScheduleOpen(true)}>
              <Calendar className="mr-2 h-4 w-4" />
              Schedule
            </Button>
            <Button onClick={handlePublish} disabled={publishMutation.isPending || updateMutation.isPending}>
              <Send className="mr-2 h-4 w-4" />
              {publishMutation.isPending ? 'Publishing...' : 'Publish Now'}
            </Button>
          </>
        )}
      </div>

      {/* Schedule dialog */}
      <Dialog open={scheduleOpen} onOpenChange={setScheduleOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Schedule Post</DialogTitle></DialogHeader>
          <div className="py-4">
            <label className="block text-sm font-medium mb-2">Schedule for</label>
            <Input
              type="datetime-local"
              value={scheduleDate}
              onChange={(e) => setScheduleDate(e.target.value)}
              min={new Date().toISOString().slice(0, 16)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setScheduleOpen(false)}>Cancel</Button>
            <Button onClick={handleSchedule} disabled={!scheduleDate || updateMutation.isPending}>
              Schedule
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
