'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Plus, Trash2, Loader2, Save, Send, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { useAccounts, useCreatePost } from '@/hooks/useQueries'
import { contentApi } from '@/services/api'
import type { SocialAccount } from '@/types'
import toast from 'react-hot-toast'

type Platform = SocialAccount['platform']
const POLL_PLATFORMS: Platform[] = ['twitter', 'linkedin']
const PLATFORM_ICONS: Record<string, string> = { twitter: '𝕏', linkedin: 'in' }
const PLATFORM_COLORS: Record<string, string> = { twitter: 'bg-sky-500', linkedin: 'bg-blue-600' }
const PLATFORM_NAMES: Record<string, string> = { twitter: 'Twitter/X', linkedin: 'LinkedIn' }

const DURATION_OPTIONS = [
  { label: '1 day', value: '1' },
  { label: '3 days', value: '3' },
  { label: '7 days', value: '7' },
]

function cn(...c: (string | undefined | false)[]) { return c.filter(Boolean).join(' ') }

export default function NewPollPage() {
  const router = useRouter()
  const { data: accounts } = useAccounts()
  const createPostMutation = useCreatePost()

  const connectedPlatforms = (accounts as SocialAccount[] | undefined)
    ?.map(a => a.platform).filter(p => POLL_PLATFORMS.includes(p)) || []

  const [selectedPlatforms, setSelectedPlatforms] = useState<Platform[]>([])
  const [question, setQuestion] = useState('')
  const [options, setOptions] = useState(['', ''])
  const [duration, setDuration] = useState('3')
  const [context, setContext] = useState('')
  const [publishing, setPublishing] = useState(false)

  const togglePlatform = (p: Platform) => {
    if (!connectedPlatforms.includes(p)) { toast.error(`Connect your ${PLATFORM_NAMES[p]} account first`); return }
    setSelectedPlatforms(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])
  }

  const addOption = () => {
    if (options.length >= 4) { toast.error('Maximum 4 options'); return }
    setOptions(prev => [...prev, ''])
  }

  const removeOption = (i: number) => {
    if (options.length <= 2) { toast.error('Need at least 2 options'); return }
    setOptions(prev => prev.filter((_, idx) => idx !== i))
  }

  const updateOption = (i: number, val: string) => {
    setOptions(prev => prev.map((o, idx) => idx === i ? val : o))
  }

  const handleSave = async (asDraft: boolean) => {
    if (!question.trim()) { toast.error('Enter a poll question'); return }
    const filledOptions = options.filter(o => o.trim())
    if (filledOptions.length < 2) { toast.error('Add at least 2 options'); return }
    if (selectedPlatforms.length === 0) { toast.error('Select at least one platform'); return }
    if (selectedPlatforms.includes('twitter') && question.length > 280) {
      toast.error('Question exceeds 280 character limit for Twitter/X'); return
    }

    setPublishing(true)
    try {
      const connectedAccounts = (accounts as SocialAccount[] | undefined) || []
      const targets = connectedAccounts
        .filter(a => selectedPlatforms.includes(a.platform))
        .map(a => ({ social_account_id: a.id }))

      const contentText = context.trim()
        ? `${context}\n\n${question}\n${filledOptions.map((o, i) => `${i + 1}. ${o}`).join('\n')}`
        : `${question}\n${filledOptions.map((o, i) => `${i + 1}. ${o}`).join('\n')}`

      const post = await createPostMutation.mutateAsync({
        content_text: contentText,
        hashtags: [],
        targets,
        metadata: {
          post_type: 'poll',
          poll: {
            question,
            options: filledOptions,
            duration_days: parseInt(duration),
            context: context.trim() || undefined,
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
        toast.success('Poll published!')
      } else {
        toast.success('Poll saved as draft')
      }
      router.push('/content')
    } catch { toast.error('Failed to save poll') }
    finally { setPublishing(false) }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">New Poll</h1>
          <p className="text-muted-foreground text-sm">Ask your audience a question</p>
        </div>
      </div>

      {/* Platform */}
      <Card>
        <CardHeader><CardTitle className="text-sm">Platform</CardTitle></CardHeader>
        <CardContent>
          <div className="flex gap-3 flex-wrap">
            {POLL_PLATFORMS.map(p => {
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

      {/* Poll builder */}
      <Card>
        <CardHeader><CardTitle className="text-sm">Poll Content</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          {/* Optional context */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Context (optional)</label>
            <Textarea
              value={context}
              onChange={e => setContext(e.target.value)}
              placeholder="Add some context or a hook before the question…"
              rows={2}
              className="resize-none"
            />
          </div>

          {/* Question */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Question *</label>
            <Input
              value={question}
              onChange={e => setQuestion(e.target.value)}
              placeholder="What do you want to ask?"
              maxLength={selectedPlatforms.includes('twitter') ? 280 : 500}
            />
            {selectedPlatforms.includes('twitter') && (
              <p className="text-xs text-muted-foreground">{question.length}/280</p>
            )}
          </div>

          {/* Options */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Options *</label>
            {options.map((opt, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground w-5 text-right">{i + 1}.</span>
                <Input
                  value={opt}
                  onChange={e => updateOption(i, e.target.value)}
                  placeholder={`Option ${i + 1}`}
                  maxLength={25}
                  className="flex-1"
                />
                <span className="text-xs text-muted-foreground w-8">{opt.length}/25</span>
                {options.length > 2 && (
                  <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive" onClick={() => removeOption(i)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            ))}
            {options.length < 4 && (
              <Button variant="outline" size="sm" onClick={addOption} className="mt-1">
                <Plus className="h-3.5 w-3.5 mr-1" />
                Add option
              </Button>
            )}
          </div>

          {/* Duration */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Duration</label>
            <Select value={duration} onValueChange={setDuration}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DURATION_OPTIONS.map(d => (
                  <SelectItem key={d.value} value={d.value}>{d.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {/* Preview */}
      {question.trim() && options.some(o => o.trim()) && (
        <Card className="border-dashed">
          <CardHeader><CardTitle className="text-sm text-muted-foreground">Preview</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            {context.trim() && <p className="text-sm">{context}</p>}
            <p className="font-medium">{question}</p>
            <div className="space-y-2">
              {options.filter(o => o.trim()).map((o, i) => (
                <div key={i} className="flex items-center gap-3 rounded-lg border px-4 py-2.5">
                  <div className="h-4 w-4 rounded-full border-2 border-primary flex-shrink-0" />
                  <span className="text-sm">{o}</span>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">{duration} day{duration !== '1' ? 's' : ''} · 0 votes</p>
          </CardContent>
        </Card>
      )}

      {/* Actions */}
      <div className="flex gap-3 justify-end border-t pt-4">
        <Button variant="outline" onClick={() => handleSave(true)} disabled={publishing}>
          <Save className="h-4 w-4 mr-2" />Save Draft
        </Button>
        <Button onClick={() => handleSave(false)} disabled={publishing}>
          {publishing ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Send className="h-4 w-4 mr-2" />}
          Publish Poll
        </Button>
      </div>
    </div>
  )
}
