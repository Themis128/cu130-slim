'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Sparkles,
  Send,
  Loader2,
  RefreshCw,
  Hash,
  ThumbsUp,
  MessageCircle,
  Share2,
  Send as SendIcon,
  Globe,
  AlertCircle,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { Switch } from '@/components/ui/Switch'
import { EmptyState } from '@/components/ui/EmptyState'
import { Label } from '@/components/ui/Label'
import { Separator } from '@/components/ui/Separator'
import { Skeleton } from '@/components/ui/Skeleton'
import { useAccounts, useLinkedinGeneratePost, useLinkedinImprovePost, useLinkedinGenerateHashtags, useLinkedinBestTime, useLinkedinPublish } from '@/hooks/useQueries'
import type { SocialAccount } from '@/types'
import toast from 'react-hot-toast'
import Link from 'next/link'

const TONES = [
  { value: 'professional', label: 'Professional' },
  { value: 'friendly', label: 'Friendly' },
  { value: 'inspiring', label: 'Inspiring' },
  { value: 'casual', label: 'Casual' },
]

const LENGTHS = [
  { value: 'short', label: 'Short' },
  { value: 'medium', label: 'Medium' },
  { value: 'long', label: 'Long' },
]

function LinkedInPreview({ content, accountName }: { content: string; accountName: string }) {
  const truncated = content.length > 250
  const [expanded, setExpanded] = useState(false)
  const display = truncated && !expanded ? content.slice(0, 250) + '...' : content

  return (
    <div className="rounded-xl border bg-white dark:bg-zinc-900 shadow-sm overflow-hidden text-[13px] max-w-xl">
      <div className="p-4">
        <div className="flex items-start gap-3 mb-3">
          <div className="w-10 h-10 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 flex items-center justify-center text-xs font-bold">
            {accountName ? accountName[0].toUpperCase() : 'L'}
          </div>
          <div>
            <p className="font-semibold text-zinc-900 dark:text-zinc-100 leading-tight">{accountName || 'LinkedIn Page'}</p>
            <p className="text-zinc-400 dark:text-zinc-500 text-[11px] flex items-center gap-1">Just now • <Globe className="h-2.5 w-2.5" /></p>
          </div>
        </div>
        <p className="whitespace-pre-wrap text-zinc-800 dark:text-zinc-200 leading-relaxed">
          {content ? display : <span className="text-zinc-400 italic">Start typing to preview...</span>}
        </p>
        {truncated && !expanded && (
          <button onClick={() => setExpanded(true)} className="text-blue-600 font-medium mt-0.5 hover:underline text-[12px]">...more</button>
        )}
      </div>
      <div className="px-4 py-2 border-t flex gap-1 text-zinc-500 text-[11px]">
        <span className="flex-1 flex items-center justify-center gap-1 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded-lg font-medium"><ThumbsUp className="h-3.5 w-3.5" /> Like</span>
        <span className="flex-1 flex items-center justify-center gap-1 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded-lg font-medium"><MessageCircle className="h-3.5 w-3.5" /> Comment</span>
        <span className="flex-1 flex items-center justify-center gap-1 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded-lg font-medium"><Share2 className="h-3.5 w-3.5" /> Repost</span>
        <span className="flex-1 flex items-center justify-center gap-1 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded-lg font-medium"><SendIcon className="h-3.5 w-3.5" /> Send</span>
      </div>
    </div>
  )
}

export default function LinkedInPage() {
  const router = useRouter()
  const { data: accounts, isLoading: accountsLoading } = useAccounts()
  const generatePost = useLinkedinGeneratePost()
  const improvePost = useLinkedinImprovePost()
  const generateHashtags = useLinkedinGenerateHashtags()
  const bestTime = useLinkedinBestTime()
  const publish = useLinkedinPublish()

  const [topic, setTopic] = useState('')
  const [tone, setTone] = useState('professional')
  const [length, setLength] = useState('medium')
  const [includeHashtags, setIncludeHashtags] = useState(true)
  const [includeSiteLink, setIncludeSiteLink] = useState(true)
  const [site, setSite] = useState('www.cloudless.gr')
  const [content, setContent] = useState('')
  const [hashtags, setHashtags] = useState<string[]>([])
  const [selectedAccountId, setSelectedAccountId] = useState('')
  const [bestTimes, setBestTimes] = useState<Array<{ day: string; time: string; confidence: string }> | null>(null)

  const linkedinAccounts = useMemo(
    () => (accounts?.accounts ?? []).filter((a: SocialAccount) => a.platform === 'linkedin'),
    [accounts]
  )

  const selectedAccount = useMemo(
    () => linkedinAccounts.find((a: SocialAccount) => a.id === selectedAccountId),
    [linkedinAccounts, selectedAccountId]
  )

  useEffect(() => {
    if (linkedinAccounts.length && !selectedAccountId) {
      const org = linkedinAccounts.find((a: SocialAccount) => a.account_type === 'organization')
      setSelectedAccountId(org?.id || linkedinAccounts[0].id)
    }
  }, [linkedinAccounts, selectedAccountId])

  useEffect(() => {
    if (!selectedAccountId) return
    bestTime.mutate(
      { account_type: selectedAccount?.account_type || 'organization' },
      {
        onSuccess: (response) => {
          setBestTimes((response.data as { best_times?: Array<{ day: string; time: string; confidence: string }> })?.best_times ?? null)
        },
      }
    )
  }, [selectedAccountId, selectedAccount])

  const onGenerate = () => {
    if (!topic.trim()) {
      toast.error('Enter a topic first')
      return
    }
    generatePost.mutate(
      {
        topic,
        tone,
        length,
        include_hashtags: includeHashtags,
        include_site_link: includeSiteLink,
        site,
      },
      {
        onSuccess: (response) => {
          const data = response.data as { caption?: string; content?: string; hashtags?: string[] }
          setContent(data.caption || data.content || '')
          setHashtags(data.hashtags || [])
          toast.success('Post generated')
        },
        onError: (error: unknown) => {
          const axiosError = error as { response?: { data?: { detail?: string } } }
          toast.error(axiosError.response?.data?.detail || 'Generation failed')
        },
      }
    )
  }

  const onImprove = () => {
    if (!content.trim()) return
    improvePost.mutate(
      { content, goal: 'engagement', tone },
      {
        onSuccess: (response) => {
          const data = response.data as { improved_content: string; hashtags: string[] }
          setContent(data.improved_content)
          if (data.hashtags?.length) setHashtags(data.hashtags)
          toast.success('Post improved')
        },
      }
    )
  }

  const onGenerateHashtags = () => {
    if (!content.trim()) return
    generateHashtags.mutate(
      { content, count: 5 },
      {
        onSuccess: (response) => {
          setHashtags((response.data as { hashtags: string[] }).hashtags)
          toast.success('Hashtags generated')
        },
      }
    )
  }

  const onPublish = () => {
    if (!selectedAccountId) {
      toast.error('Select a LinkedIn account first')
      return
    }
    if (!content.trim()) {
      toast.error('Generate or write content first')
      return
    }
    publish.mutate(
      { account_id: selectedAccountId, commentary: content },
      {
        onSuccess: (response) => {
          const data = response.data as { platform_url?: string; platform_post_id?: string }
          toast.success(data.platform_url ? 'Published!' : 'Published to LinkedIn')
          router.push(data.platform_url || '/content')
        },
      }
    )
  }

  if (accountsLoading) {
    return (
      <div className="space-y-6 max-w-4xl">
        <Skeleton className="h-8 w-48" />
        <div className="grid gap-6 md:grid-cols-2">
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      </div>
    )
  }

  if (!linkedinAccounts.length) {
    return (
      <EmptyState
        icon={AlertCircle}
        title="No LinkedIn account connected"
        description="Connect a LinkedIn Company Page or profile before creating LinkedIn content."
        primaryAction={{ label: 'Connect LinkedIn', href: '/accounts', icon: Sparkles }}
        className="py-16"
      />
    )
  }

  const captionWithHashtags = content + (hashtags.length ? `\n\n${hashtags.map((h) => `#${h.replace(/^#/, '')}`).join(' ')}` : '')

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">LinkedIn Post</h1>
          <p className="text-muted-foreground mt-1">Generate and publish LinkedIn-first content with AI.</p>
        </div>
        <Button asChild variant="outline">
          <Link href="/content/new">Use generic editor</Link>
        </Button>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>What is this post about?</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Label htmlFor="topic">Topic</Label>
                <Input
                  id="topic"
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder="e.g. How cloudless.gr helps teams ship serverless apps"
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <Label>Tone</Label>
                  <Select value={tone} onValueChange={setTone}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TONES.map((t) => (
                        <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label>Length</Label>
                  <Select value={length} onValueChange={setLength}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {LENGTHS.map((l) => (
                        <SelectItem key={l.value} value={l.value}>{l.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2 items-end">
                <div className="flex items-center gap-2">
                  <Switch id="hashtags" checked={includeHashtags} onCheckedChange={setIncludeHashtags} />
                  <Label htmlFor="hashtags" className="cursor-pointer">Include hashtags</Label>
                </div>
                <div className="flex items-center gap-2">
                  <Switch id="site-link" checked={includeSiteLink} onCheckedChange={setIncludeSiteLink} />
                  <Label htmlFor="site-link" className="cursor-pointer">Link to {site}</Label>
                </div>
              </div>

              {includeSiteLink && (
                <div>
                  <Label htmlFor="site">Site / landing page</Label>
                  <Input id="site" value={site} onChange={(e) => setSite(e.target.value)} />
                </div>
              )}

              <Button onClick={onGenerate} disabled={generatePost.isPending || !topic.trim()} className="w-full">
                {generatePost.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                Generate LinkedIn post
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Content</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={10}
                placeholder="Generated content will appear here. You can edit it before publishing."
              />

              <div className="flex flex-wrap gap-2">
                {hashtags.map((tag) => (
                  <Badge key={tag} variant="secondary" className="cursor-pointer" onClick={() => setHashtags(hashtags.filter((h) => h !== tag))}>
                    #{tag.replace(/^#/, '')}
                  </Badge>
                ))}
              </div>

              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={onImprove} disabled={improvePost.isPending || !content.trim()}>
                  <RefreshCw className={`mr-2 h-4 w-4 ${improvePost.isPending ? 'animate-spin' : ''}`} /> Improve
                </Button>
                <Button variant="outline" onClick={onGenerateHashtags} disabled={generateHashtags.isPending || !content.trim()}>
                  <Hash className="mr-2 h-4 w-4" /> Hashtags
                </Button>
              </div>

              <div>
                <Label htmlFor="account">Target account</Label>
                <Select value={selectedAccountId} onValueChange={setSelectedAccountId}>
                  <SelectTrigger id="account">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {linkedinAccounts.map((account: SocialAccount) => (
                      <SelectItem key={account.id} value={account.id}>
                        {account.display_name || account.username || account.account_id} {account.account_type === 'organization' ? '(Company Page)' : '(Personal)'}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <Button onClick={onPublish} disabled={publish.isPending || !content.trim() || !selectedAccountId} className="w-full">
                {publish.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}
                Publish to LinkedIn
              </Button>

              {bestTimes && (
                <div className="text-xs text-muted-foreground">
                  Best times: {bestTimes.map((w) => `${w.day} ${w.time} (${w.confidence})`).join(', ')}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Preview</CardTitle>
            </CardHeader>
            <CardContent>
              <LinkedInPreview content={captionWithHashtags} accountName={selectedAccount?.display_name || selectedAccount?.username || 'Cloudless'} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>About this page</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Posts target your connected LinkedIn Company Page by default (e.g. <code>4a8d9440-47d2-4bda-bd11-3776fd9022ba</code>). For carousels, use <Link href="/content/carousel/new" className="text-primary hover:underline">Carousels</Link>.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
