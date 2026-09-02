'use client'

import { useState, useEffect, useRef } from 'react'
import { useRouter, useParams } from 'next/navigation'
import { ArrowLeft, Save, Send, Trash2, X, Image as ImageIcon, Calendar, Loader2, Heart, MessageCircle, Share2, Eye, TrendingUp, CheckCircle2, XCircle, MessageSquare, Clock } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/Dialog'
import { Skeleton } from '@/components/ui/Skeleton'
import { usePost, useUpdatePost, useDeletePost, usePublishPost, useUploadMedia, usePostAnalytics, useSubmitForReview, useApprovePost, useRejectPost, useAddComment, usePillars, useBriefs } from '@/hooks/useQueries'
import { contentApi } from '@/services/api'
import type { Post } from '@/types'
import Link from 'next/link'
import toast from 'react-hot-toast'
import { Undo2 } from 'lucide-react'
import { SpellCheckButton } from '@/components/content/SpellCheckButton'
import { SeoPanel } from '@/components/content/SeoPanel'
import { ObjectUrlImage } from '@/components/content/previewIdentity'
import { athensDateTimeLocalToIso, toAthensDateTimeLocal } from '@/lib/utils'

export default function EditPostPage() {
  const router = useRouter()
  const params = useParams()
  const id = (params?.id ?? '') as string

  const { data: post, isLoading } = usePost(id)
  const { data: analytics } = usePostAnalytics(id)
  const updateMutation = useUpdatePost()
  const deleteMutation = useDeletePost()
  const publishMutation = usePublishPost()
  const uploadMediaMutation = useUploadMedia()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const submitReviewMutation = useSubmitForReview()
  const approveMutation = useApprovePost()
  const rejectMutation = useRejectPost()
  const addCommentMutation = useAddComment()
  const { data: pillars } = usePillars()
  const { data: briefs } = useBriefs()

  const [content, setContent] = useState('')
  const [hashtags, setHashtags] = useState<string[]>([])
  const [hashtagInput, setHashtagInput] = useState('')
  const [linkUrl, setLinkUrl] = useState('')
  const [mediaIds, setMediaIds] = useState<string[]>([])
  const [newMediaFiles, setNewMediaFiles] = useState<File[]>([])
  const [scheduleDate, setScheduleDate] = useState('')
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const [pillarId, setPillarId] = useState<string | null>(null)
  const [briefId, setBriefId] = useState<string | null>(null)
  const [commentText, setCommentText] = useState('')
  const [rejectOpen, setRejectOpen] = useState(false)
  const [rejectReason, setRejectReason] = useState('')

  useEffect(() => {
    if (post) {
      setContent((post as Post).content_text || '')
      setHashtags((post as Post).hashtags || [])
      setLinkUrl((post as Post).link_url || '')
      setMediaIds(((post as Post).media_ids || []).map(String))
      setScheduleDate((post as Post).scheduled_at ? toAthensDateTimeLocal((post as Post).scheduled_at!) : '')
      setPillarId((post as Post & { pillar_id?: string | null })?.pillar_id ?? null)
      setBriefId((post as Post & { content_brief_id?: string | null })?.content_brief_id ?? null)
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
          pillar_id: pillarId,
          content_brief_id: briefId,
        } as Partial<Post>,
      })
      router.push('/content')
    } catch {
      // mutation already toasts
    }
  }

  const handleSubmitReview = async () => {
    try {
      await updateMutation.mutateAsync({
        id,
        data: { content_text: content, hashtags, link_url: linkUrl || undefined, pillar_id: pillarId, content_brief_id: briefId } as Partial<Post>,
      })
      await submitReviewMutation.mutateAsync(id)
    } catch {
      // mutation already toasts
    }
  }

  const handleApprove = async () => {
    try {
      await approveMutation.mutateAsync({ id })
    } catch {
      // mutation already toasts
    }
  }

  const handleReject = async () => {
    try {
      await rejectMutation.mutateAsync({ id, body: rejectReason || 'Needs revision' })
      setRejectOpen(false)
      setRejectReason('')
    } catch {
      // mutation already toasts
    }
  }

  const handleAddComment = async () => {
    if (!commentText.trim()) return
    try {
      await addCommentMutation.mutateAsync({ id, body: commentText })
      setCommentText('')
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
      const scheduledIso = athensDateTimeLocalToIso(scheduleDate)
      await updateMutation.mutateAsync({
        id,
        data: { content_text: content, hashtags, scheduled_at: scheduledIso } as Partial<Post>,
      })
      await contentApi.schedulePost(id, scheduledIso)
      setScheduleOpen(false)
      router.push('/content')
    } catch {
      toast.error('Failed to schedule')
    }
  }

  const handleDelete = () => {
    const DELAY = 5000
    let cancelled = false
    const toastId = `delete-post-${id}`

    const commit = async () => {
      if (cancelled) return
      toast.dismiss(toastId)
      try {
        await deleteMutation.mutateAsync(id)
        router.push('/content')
      } catch (err: unknown) {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        toast.error(detail ?? 'Failed to delete post')
      }
    }

    const timer = setTimeout(commit, DELAY)

    toast.custom(
      (t) => (
        <div className={[
          'flex flex-col w-72 rounded-lg border bg-background shadow-lg overflow-hidden transition-all duration-200',
          t.visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2',
        ].join(' ')}>
          <div className="flex items-center justify-between gap-3 px-4 py-3">
            <span className="text-sm font-medium">Post will be deleted</span>
            <button
              onClick={() => { cancelled = true; clearTimeout(timer); toast.dismiss(toastId) }}
              className="flex items-center gap-1.5 text-sm font-semibold text-primary hover:opacity-80 transition-opacity"
            >
              <Undo2 className="h-3.5 w-3.5" />
              Undo
            </button>
          </div>
          <div className="h-0.5 bg-muted"><div className="h-full bg-primary" style={{ animation: `undo-drain ${DELAY}ms linear forwards` }} /></div>
          <style>{`@keyframes undo-drain { from { width:100% } to { width:0% } }`}</style>
        </div>
      ),
      { id: toastId, duration: DELAY + 400 }
    )
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
          <SpellCheckButton text={content} onApply={setContent} />
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
                    <ObjectUrlImage file={file} alt={file.name} className="h-full w-full object-cover" />
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Performance card — only for published posts with analytics */}
      {typedPost.status === 'published' && (() => {
        const a = analytics as { likes?: number; comments?: number; shares?: number; impressions?: number; reach?: number; engagement_rate?: number } | undefined
        if (!a) return null
        const likes = a.likes ?? 0
        const comments = a.comments ?? 0
        const shares = a.shares ?? 0
        const impressions = a.impressions ?? 0
        const totalEngagement = likes + comments + shares
        const engRate = a.engagement_rate ?? (impressions > 0 ? (totalEngagement / impressions) * 100 : 0)
        // Visual score: 0-100 based on engagement rate benchmarks
        // avg industry: 1-3% is good, 5%+ excellent
        const score = Math.min(100, Math.round((engRate / 5) * 100))
        const scoreColor = score >= 80 ? '#22c55e' : score >= 50 ? '#f59e0b' : score >= 20 ? '#3b82f6' : '#94a3b8'
        const scoreLabel = score >= 80 ? 'Excellent' : score >= 50 ? 'Good' : score >= 20 ? 'Average' : 'Low'
        const circR = 44
        const circ = 2 * Math.PI * circR
        const stats = [
          { icon: Heart,          label: 'Likes',       value: likes.toLocaleString(),        color: 'text-red-500',  bg: 'bg-red-500/10' },
          { icon: MessageCircle,  label: 'Comments',    value: comments.toLocaleString(),      color: 'text-blue-500', bg: 'bg-blue-500/10' },
          { icon: Share2,         label: 'Shares',      value: shares.toLocaleString(),        color: 'text-green-500',bg: 'bg-green-500/10' },
          { icon: Eye,            label: 'Impressions', value: impressions.toLocaleString(),   color: 'text-purple-500',bg:'bg-purple-500/10' },
        ]
        return (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <TrendingUp className="h-4 w-4 text-primary" />
                Post Performance
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-col sm:flex-row gap-6 items-center">
                {/* Score ring */}
                <div className="flex flex-col items-center gap-2 flex-shrink-0">
                  <div className="relative">
                    <svg width={104} height={104} className="-rotate-90">
                      <circle cx={52} cy={52} r={circR} fill="none" stroke="currentColor" strokeWidth={8} className="text-muted/20" />
                      <circle
                        cx={52} cy={52} r={circR}
                        fill="none" stroke={scoreColor} strokeWidth={8}
                        strokeDasharray={circ}
                        strokeDashoffset={circ * (1 - score / 100)}
                        strokeLinecap="round"
                        style={{ transition: 'stroke-dashoffset 0.6s ease' }}
                      />
                    </svg>
                    <div className="absolute inset-0 flex flex-col items-center justify-center">
                      <span className="text-2xl font-bold tabular-nums" style={{ color: scoreColor }}>{score}</span>
                      <span className="text-[10px] text-muted-foreground">/ 100</span>
                    </div>
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-center" style={{ color: scoreColor }}>{scoreLabel}</p>
                    <p className="text-xs text-muted-foreground text-center">{engRate.toFixed(2)}% eng. rate</p>
                  </div>
                </div>

                {/* Stat tiles */}
                <div className="grid grid-cols-2 gap-3 flex-1 w-full">
                  {stats.map(({ icon: Icon, label, value, color, bg }) => (
                    <div key={label} className="flex items-center gap-3 rounded-lg border p-3">
                      <div className={`p-2 rounded-full ${bg}`}>
                        <Icon className={`h-4 w-4 ${color}`} />
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">{label}</p>
                        <p className="text-lg font-bold tabular-nums">{value}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        )
      })()}

      {/* SEO Analysis */}
      {content.trim().length > 20 && (
        <SeoPanel
          content={content}
          platform={typedPost.targets?.[0]?.social_account?.platform || 'linkedin'}
        />
      )}

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
            <label className="block text-sm font-medium mb-2">Schedule for (Europe/Athens)</label>
            <Input
              type="datetime-local"
              value={scheduleDate}
              onChange={(e) => setScheduleDate(e.target.value)}
              min={toAthensDateTimeLocal(new Date())}
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
