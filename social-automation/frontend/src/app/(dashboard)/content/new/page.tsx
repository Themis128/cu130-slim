'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { X, Image, Smile, Hash, Sparkles, Loader2, Send, Calendar, Save } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/Dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator, DropdownMenuCheckboxItem } from '@/components/ui/DropdownMenu'
import { Badge } from '@/components/ui/Badge'
import { Separator } from '@/components/ui/Separator'
import { Input } from '@/components/ui/Input'
import { useAccounts } from '@/hooks/useQueries'
import type { SocialAccount } from '@/types'
import { useCreatePost, useUploadMedia } from '@/hooks/useQueries'
import { useAuth } from '@/hooks/useAuth'
import toast from 'react-hot-toast'

const platforms = [
  { id: 'linkedin', name: 'LinkedIn', icon: 'in', color: 'bg-blue-600', maxChars: 3000 },
  { id: 'twitter', name: 'Twitter/X', icon: '𝕏', color: 'bg-sky-500', maxChars: 280 },
  { id: 'instagram', name: 'Instagram', icon: '📷', color: 'bg-pink-500', maxChars: 2200 },
  { id: 'facebook', name: 'Facebook', icon: 'f', color: 'bg-blue-700', maxChars: 63206 },
  { id: 'threads', name: 'Threads', icon: '@', color: 'bg-gray-800', maxChars: 500 },
]

export default function NewPostPage() {
  const router = useRouter()
  const { user } = useAuth()
  const { data: accounts } = useAccounts()
  const createPostMutation = useCreatePost()
  const uploadMediaMutation = useUploadMedia()

  const [content, setContent] = useState('')
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([])
  const [mediaFiles, setMediaFiles] = useState<File[]>([])
  const [isScheduling, setIsScheduling] = useState(false)
  const [scheduleDate, setScheduleDate] = useState('')
  const [aiGenerating, setAiGenerating] = useState(false)

  const connectedPlatforms = accounts?.map((a: SocialAccount) => a.platform) || []

  const handlePlatformToggle = (platformId: string) => {
    if (!connectedPlatforms.includes(platformId)) {
      toast.error(`Connect your ${platformId} account first`)
      return
    }
    setSelectedPlatforms(prev =>
      prev.includes(platformId)
        ? prev.filter(p => p !== platformId)
        : [...prev, platformId]
    )
  }

  const handleMediaUpload = async (files: FileList) => {
    const newFiles = Array.from(files)
    if (mediaFiles.length + newFiles.length > 10) {
      toast.error('Maximum 10 media files allowed')
      return
    }
    for (const file of newFiles) {
      try {
        await uploadMediaMutation.mutateAsync({ file, alt_text: '', tags: '' })
        setMediaFiles(prev => [...prev, file])
      } catch {
        toast.error(`Failed to upload ${file.name}`)
      }
    }
  }

  const handleRemoveMedia = (index: number) => {
    setMediaFiles(prev => prev.filter((_, i) => i !== index))
  }

  const handleSubmit = async (e: React.FormEvent, action: 'draft' | 'publish' | 'schedule') => {
    e.preventDefault()
    if (!content.trim()) {
      toast.error('Please add some content')
      return
    }
    if (selectedPlatforms.length === 0) {
      toast.error('Select at least one platform')
      return
    }
    if (action === 'schedule' && !scheduleDate) {
      toast.error('Select a date and time')
      return
    }

    try {
      const postData = {
        content,
        platforms: selectedPlatforms,
        media_urls: mediaFiles.map((_, i) => `media-${i}`), // placeholder, actual URLs would come from upload
        status: action === 'draft' ? 'draft' : action === 'schedule' ? 'scheduled' : 'publishing',
        scheduled_at: action === 'schedule' ? new Date(scheduleDate).toISOString() : undefined,
      }
      await createPostMutation.mutateAsync(postData)
      toast.success(action === 'draft' ? 'Draft saved' : action === 'schedule' ? 'Post scheduled' : 'Post published')
      router.push('/content')
    } catch {
      toast.error('Failed to create post')
    }
  }

  const handleAIGenerate = async () => {
    setAiGenerating(true)
    try {
      // In a real app, this would call the AI API
      // For now, we'll simulate with a placeholder
      await new Promise(r => setTimeout(r, 1000))
      setContent('🚀 Exciting news! We\'re launching our new social media automation platform. Stay tuned for more updates! #SocialMedia #Automation #Tech')
      toast.success('AI content generated')
    } catch {
      toast.error('Failed to generate content')
    } finally {
      setAiGenerating(false)
    }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">New Post</h1>
          <p className="text-muted-foreground mt-1">Create and schedule content across platforms</p>
        </div>
      </div>

      {/* Platform selector */}
      <Card>
        <CardHeader>
          <CardTitle>Select Platforms</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-3">
            {platforms.map((platform) => {
              const isConnected = connectedPlatforms.includes(platform.id)
              const isSelected = selectedPlatforms.includes(platform.id)
              return (
                <Button
                  key={platform.id}
                  variant={isSelected ? 'default' : 'outline'}
                  className={cn(
                    'flex items-center gap-2',
                    !isConnected && 'opacity-50 cursor-not-allowed'
                  )}
                  onClick={() => handlePlatformToggle(platform.id)}
                  disabled={!isConnected}
                >
                  <span
                    className={cn(
                      'w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white',
                      platform.color
                    )}
                  >
                    {platform.icon}
                  </span>
                  <span>{platform.name}</span>
                  {!isConnected && (
                    <Badge variant="outline" className="ml-1 text-xs">Connect</Badge>
                  )}
                </Button>
              )
            })}
          </div>
          {selectedPlatforms.length === 0 && (
            <p className="text-sm text-muted-foreground mt-2">
              Connect accounts in <Button variant="ghost" size="sm" asChild>
                <a href="/accounts">Settings</a>
              </Button>
            </p>
          )}
        </CardContent>
      </Card>

      {/* Content editor */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Content</CardTitle>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={handleAIGenerate} disabled={aiGenerating}>
              <Sparkles className="mr-2 h-4 w-4" />
              {aiGenerating ? 'Generating...' : 'AI Assist'}
            </Button>
            <div className="text-sm text-muted-foreground">
              {content.length} / {Math.min(...selectedPlatforms.map(p => platforms.find(pl => pl.id === p)?.maxChars || 3000))} chars
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="What do you want to share?"
            className="min-h-[200px] font-mono text-base"
            rows={8}
          />
          <div className="flex items-center justify-between mt-3">
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => {}}>
                <Image className="mr-2 h-4 w-4" />
                Add Media
              </Button>
              <Button variant="outline" size="sm">
                <Smile className="mr-2 h-4 w-4" />
                Emoji
              </Button>
              <Button variant="outline" size="sm">
                <Hash className="mr-2 h-4 w-4" />
                Hashtags
              </Button>
            </div>
          </div>

          {/* Media preview */}
          {mediaFiles.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {mediaFiles.map((file, index) => (
                <div key={index} className="relative h-20 w-20 rounded-lg overflow-hidden border">
                  {file.type.startsWith('image/') && (
                    <img
                      src={URL.createObjectURL(file)}
                      alt="Preview"
                      className="h-full w-full object-cover"
                    />
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    className="absolute top-1 right-1 h-6 w-6 bg-black/50 text-white"
                    onClick={() => handleRemoveMedia(index)}
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
              <label className="h-20 w-20 rounded-lg border-2 border-dashed border-muted flex items-center justify-center cursor-pointer">
                <input
                  type="file"
                  accept="image/*,video/*"
                  multiple
                  className="hidden"
                  onChange={(e) => e.target.files && handleMediaUpload(e.target.files)}
                />
                <span className="text-muted-foreground">+</span>
              </label>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Platform-specific previews */}
      {selectedPlatforms.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Previews</CardTitle>
          </CardHeader>
          <CardContent>
            <Tabs defaultValue={selectedPlatforms[0]}>
              <TabsList className="grid w-full grid-cols-5">
                {selectedPlatforms.map((platformId) => {
                  const platform = platforms.find(p => p.id === platformId)
                  return (
                    <TabsTrigger key={platformId} value={platformId}>
                      {platform?.name}
                    </TabsTrigger>
                  )
                })}
              </TabsList>
              {selectedPlatforms.map((platformId) => {
                const platform = platforms.find(p => p.id === platformId)
                const maxChars = platform?.maxChars || 3000
                const charCount = content.length
                const isOverLimit = charCount > maxChars
                return (
                  <TabsContent key={platformId} value={platformId} className="mt-4 p-4 border rounded-lg bg-muted/30">
                    <div className="prose max-w-none">
                      <p className="whitespace-pre-wrap">{content || <span className="text-muted-foreground">Start typing to see preview...</span>}</p>
                    </div>
                    <div className="mt-3 flex items-center justify-between text-sm">
                      <span className={cn(isOverLimit ? 'text-destructive' : 'text-muted-foreground')}>
                        {charCount} / {maxChars} characters
                      </span>
                      {isOverLimit && (
                        <Badge variant="destructive">Over limit</Badge>
                      )}
                    </div>
                  </TabsContent>
                )
              })}
            </Tabs>
          </CardContent>
        </Card>
      )}

      {/* Actions */}
      <div className="flex flex-col sm:flex-row gap-4 justify-end border-t pt-6">
        <Button variant="outline" onClick={(e) => handleSubmit(e, 'draft')}>
          <Save className="mr-2 h-4 w-4" />
          Save Draft
        </Button>
        <Dialog>
          <DialogTrigger asChild>
            <Button variant="outline">
              <Calendar className="mr-2 h-4 w-4" />
              Schedule
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Schedule Post</DialogTitle>
            </DialogHeader>
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
              <Button variant="outline" onClick={() => setIsScheduling(false)}>Cancel</Button>
              <Button onClick={(e) => { handleSubmit(e, 'schedule'); setIsScheduling(false); }}>
                Schedule
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <Button onClick={(e) => handleSubmit(e, 'publish')} className="bg-primary">
          <Send className="mr-2 h-4 w-4" />
          Publish Now
        </Button>
      </div>
    </div>
  )
}

function cn(...classes: (string | undefined | null | false)[]) {
  return classes.filter(Boolean).join(' ')
}