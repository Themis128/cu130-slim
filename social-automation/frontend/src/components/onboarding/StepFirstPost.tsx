'use client'

import { useState } from 'react'
import { ArrowRight, SkipForward, Send, CheckCircle2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Textarea } from '@/components/ui/Textarea'
import { cn } from '@/lib/utils'
import { contentApi } from '@/services/api'
import toast from 'react-hot-toast'
import { formatErrorToast } from '@/lib/humanizeError'

const PLATFORMS = [
  { id: 'linkedin', label: 'LinkedIn' },
  { id: 'facebook', label: 'Facebook' },
  { id: 'twitter', label: 'Twitter / X' },
  { id: 'instagram', label: 'Instagram' },
] as const

interface StepFirstPostProps {
  /** Called after a post is created or skipped */
  onContinue: () => void
}

export function StepFirstPost({ onContinue }: StepFirstPostProps) {
  const [content, setContent] = useState('')
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([])
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState(false)

  const togglePlatform = (id: string) => {
    setSelectedPlatforms(prev =>
      prev.includes(id) ? prev.filter(p => p !== id) : [...prev, id]
    )
  }

  const handleCreate = async () => {
    if (!content.trim()) {
      toast.error('Write a sentence or two to get started.')
      return
    }
    setCreating(true)
    try {
      await contentApi.createPost({
        content_text: content,
        // Save as draft — do not publish
        metadata: { source: 'onboarding' },
      })
      setCreated(true)
      toast.success('Draft saved.')
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(
        typeof detail === 'string'
          ? formatErrorToast('Couldn’t save your draft.', err)
          : formatErrorToast('Couldn’t save your draft. Please try again.', err)
      )
    } finally {
      setCreating(false)
    }
  }

  if (created) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center justify-center py-10 text-center">
          <CheckCircle2 className="h-12 w-12 text-green-500 mb-3" />
          <h3 className="text-lg font-semibold">Draft post created!</h3>
          <p className="mt-1 text-sm text-muted-foreground max-w-sm">
            Your post is saved as a draft. You can edit and schedule it from Posts.
          </p>
          <Button onClick={onContinue} className="mt-5">
            Continue
            <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Create your first post</CardTitle>
        <CardDescription>
          Write a quick post. We’ll save it as a draft so you can schedule it later.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Post content */}
        <Textarea
          label="Post content"
          placeholder="What would you like to share?"
          rows={5}
          value={content}
          onChange={e => setContent(e.target.value)}
        />

        {/* Platform selector */}
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Platforms (optional)</label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {PLATFORMS.map(p => {
              const selected = selectedPlatforms.includes(p.id)
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => togglePlatform(p.id)}
                  className={cn(
                    'flex items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-sm transition-colors',
                    selected
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-input hover:bg-accent'
                  )}
                >
                  {selected && <CheckCircle2 className="h-3.5 w-3.5" />}
                  {p.label}
                </button>
              )
            })}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between pt-2">
          <Button variant="ghost" onClick={onContinue}>
            <SkipForward className="mr-2 h-4 w-4" />
            Skip for now
          </Button>
          <Button onClick={handleCreate} isLoading={creating} disabled={!content.trim()}>
            <Send className="mr-2 h-4 w-4" />
            Create Post
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
