'use client'

import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  X, Image as ImageIcon, Sparkles, Send, Calendar, Save,
  LayoutTemplate, AlignLeft, List, BarChart2, PlaySquare, BookOpen,
  Heart, MessageCircle, Share2, Bookmark, Repeat2, MoreHorizontal,
  ThumbsUp, Globe, ChevronDown, Loader2, Copy, Layers, Shield, Music,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { SafeImage } from '@/components/ui/SafeImage'
import { Textarea } from '@/components/ui/Textarea'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '@/components/ui/Dialog'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import Link from 'next/link'
import { useAccounts, useCreatePost, useUploadMedia, useGenerateContent } from '@/hooks/useQueries'
import { contentApi, aiApi, mediaUrl, brandApi } from '@/services/api'
import { MediaPickerDialog } from '@/components/ui/MediaPickerDialog'
import { MusicPickerDialog } from '@/components/ui/MusicPickerDialog'
import type { SocialAccount, MediaAsset } from '@/types'
import { useAdvisor } from '@/hooks/useAdvisor'
import { VoiceRecorder } from '@/components/content/VoiceRecorder'
import { SpellCheckButton } from '@/components/content/SpellCheckButton'
import { SeoPanel } from '@/components/content/SeoPanel'
import {
  preferredAccount,
  identityFromAccount,
  ObjectUrlImage,
  AccountAvatar,
  isOrgAccount,
  type PreviewIdentity,
} from '@/components/content/previewIdentity'
import { cn, athensDateTimeLocalToIso, toAthensDateTimeLocal } from '@/lib/utils'
import toast from 'react-hot-toast'

type PreviewProps = { content: string; media: File[]; identity: PreviewIdentity }

const platforms = [
  { id: 'linkedin',  name: 'LinkedIn',    icon: 'in', color: 'bg-blue-600',  textColor: 'text-blue-600',  borderColor: 'border-blue-600',  maxChars: 3000  },
  { id: 'twitter',   name: 'Twitter / X', icon: '𝕏',  color: 'bg-sky-500',   textColor: 'text-sky-500',   borderColor: 'border-sky-500',   maxChars: 280   },
  { id: 'instagram', name: 'Instagram',   icon: '📷', color: 'bg-pink-500',  textColor: 'text-pink-500',  borderColor: 'border-pink-500',  maxChars: 2200  },
  { id: 'facebook',  name: 'Facebook',    icon: 'f',  color: 'bg-blue-700',  textColor: 'text-blue-700',  borderColor: 'border-blue-700',  maxChars: 63206 },
  { id: 'threads',   name: 'Threads',     icon: '@',  color: 'bg-gray-800',  textColor: 'text-gray-800',  borderColor: 'border-gray-800',  maxChars: 500   },
  { id: 'tiktok',    name: 'TikTok',      icon: 'TT', color: 'bg-black',     textColor: 'text-black',     borderColor: 'border-black',     maxChars: 2200  },
]

// SVG character ring
function CharRing({ count, max, size = 32 }: { count: number; max: number; size?: number }) {
  const r = (size - 4) / 2
  const circ = 2 * Math.PI * r
  const ratio = Math.min(count / max, 1)
  const pct = count / max
  const stroke = pct >= 1 ? '#ef4444' : pct >= 0.8 ? '#f59e0b' : '#22c55e'
  const remaining = max - count

  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeWidth={3} className="text-muted/30" />
        <circle
          cx={size / 2} cy={size / 2} r={r}
          fill="none" stroke={stroke} strokeWidth={3}
          strokeDasharray={circ}
          strokeDashoffset={circ * (1 - ratio)}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.2s ease' }}
        />
      </svg>
      {count > 0 && (
        <span className="absolute text-[9px] font-semibold" style={{ color: stroke, lineHeight: 1 }}>
          {pct >= 1 ? '!' : remaining > 99 ? '' : remaining}
        </span>
      )}
    </div>
  )
}

// Platform preview cards — identity always comes from a real SocialAccount
function LinkedInPreview({ content, media, identity }: PreviewProps) {
  const truncated = content.length > 200
  const [expanded, setExpanded] = useState(false)
  const display = truncated && !expanded ? content.slice(0, 200) + '...' : content

  return (
    <div className="rounded-xl border bg-white dark:bg-zinc-900 shadow-sm overflow-hidden text-[13px]">
      <div className="p-4">
        <div className="flex items-start gap-3 mb-3">
          <AccountAvatar
            identity={identity}
            className="w-10 h-10 rounded-full flex-shrink-0 text-xs"
            fallbackClass="bg-blue-100 dark:bg-blue-900 text-blue-600"
          />
          <div>
            <p className="font-semibold text-zinc-900 dark:text-zinc-100 leading-tight">{identity.name}</p>
            {identity.headline && (
              <p className="text-zinc-500 dark:text-zinc-400 text-[11px]">{identity.headline}</p>
            )}
            <p className="text-zinc-400 dark:text-zinc-500 text-[11px] flex items-center gap-1">Just now • <Globe className="h-2.5 w-2.5" /></p>
          </div>
          <MoreHorizontal className="ml-auto h-4 w-4 text-zinc-400" />
        </div>
        <p className="whitespace-pre-wrap text-zinc-800 dark:text-zinc-200 leading-relaxed">
          {content ? display : <span className="text-zinc-400 italic">Start typing to preview...</span>}
        </p>
        {truncated && !expanded && (
          <button onClick={() => setExpanded(true)} className="text-blue-600 font-medium mt-0.5 hover:underline text-[12px]">...more</button>
        )}
      </div>
      {media.length > 0 && (
        <div className="grid grid-cols-2 gap-0.5 mx-4 mb-3">
          {media.slice(0, 4).map((f, i) => (
            <ObjectUrlImage key={`${f.name}-${i}`} file={f} className="w-full aspect-video object-cover rounded" />
          ))}
        </div>
      )}
      <div className="px-4 py-2 border-t flex gap-1 text-zinc-500">
        {[['👍', 'Like'], ['💬', 'Comment'], ['🔄', 'Repost'], ['✉️', 'Send']].map(([emoji, label]) => (
          <button key={label} className="flex-1 flex items-center justify-center gap-1 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded-lg text-[11px] font-medium transition-colors">
            <span>{emoji}</span>{label}
          </button>
        ))}
      </div>
    </div>
  )
}

function TwitterPreview({ content, media, identity }: PreviewProps) {
  return (
    <div className="rounded-xl border bg-white dark:bg-zinc-900 shadow-sm p-4 text-[13px]">
      <div className="flex gap-3">
        <AccountAvatar
          identity={identity}
          className="w-10 h-10 rounded-full flex-shrink-0 text-xs"
          fallbackClass="bg-sky-100 dark:bg-sky-900 text-sky-500"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1 flex-wrap">
            <span className="font-bold text-zinc-900 dark:text-zinc-100">{identity.name}</span>
            <span className="text-zinc-500 dark:text-zinc-400 text-[12px]">@{identity.handle} · just now</span>
            <MoreHorizontal className="ml-auto h-4 w-4 text-zinc-400" />
          </div>
          <p className="mt-1 whitespace-pre-wrap leading-relaxed text-zinc-800 dark:text-zinc-200">
            {content || <span className="text-zinc-400 italic">Start typing to preview...</span>}
          </p>
          {media.length > 0 && (
            <div className="mt-2 grid grid-cols-2 gap-0.5 rounded-xl overflow-hidden">
              {media.slice(0, 4).map((f, i) => (
                <ObjectUrlImage key={`${f.name}-${i}`} file={f} className="w-full aspect-video object-cover" />
              ))}
            </div>
          )}
          <div className="mt-3 flex items-center gap-4 text-zinc-500">
            <button className="flex items-center gap-1 hover:text-sky-500 transition-colors"><MessageCircle className="h-3.5 w-3.5" /><span className="text-[11px]">0</span></button>
            <button className="flex items-center gap-1 hover:text-green-500 transition-colors"><Repeat2 className="h-3.5 w-3.5" /><span className="text-[11px]">0</span></button>
            <button className="flex items-center gap-1 hover:text-red-500 transition-colors"><Heart className="h-3.5 w-3.5" /><span className="text-[11px]">0</span></button>
            <button className="flex items-center gap-1 hover:text-sky-500 transition-colors"><Bookmark className="h-3.5 w-3.5" /></button>
            <button className="flex items-center gap-1 hover:text-sky-500 transition-colors ml-auto"><Share2 className="h-3.5 w-3.5" /></button>
          </div>
        </div>
      </div>
    </div>
  )
}

function InstagramPreview({ content, media, identity }: PreviewProps) {
  const hashtagRe = /#\w+/g
  const parts = content.split(hashtagRe)
  const tags = content.match(hashtagRe) || []

  return (
    <div className="rounded-xl border bg-white dark:bg-zinc-900 shadow-sm overflow-hidden text-[13px]">
      <div className="flex items-center gap-2 px-3 py-2 border-b">
        <AccountAvatar
          identity={identity}
          className="w-8 h-8 rounded-full flex-shrink-0 text-[10px]"
          fallbackClass="bg-gradient-to-br from-pink-500 to-yellow-400 text-white"
        />
        <span className="font-semibold text-zinc-900 dark:text-zinc-100 text-[12px]">{identity.handle}</span>
        <MoreHorizontal className="ml-auto h-4 w-4 text-zinc-400" />
      </div>
      <div className="aspect-square bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center">
        {media.length > 0
          ? <ObjectUrlImage file={media[0]} className="w-full h-full object-cover" />
          : <ImageIcon className="h-10 w-10 text-zinc-300" />
        }
      </div>
      <div className="px-3 pt-2 pb-1 flex gap-3 text-zinc-600 dark:text-zinc-400">
        <Heart className="h-5 w-5 hover:text-red-500 cursor-pointer transition-colors" />
        <MessageCircle className="h-5 w-5 hover:text-zinc-900 cursor-pointer transition-colors" />
        <Share2 className="h-5 w-5 hover:text-zinc-900 cursor-pointer transition-colors" />
        <Bookmark className="ml-auto h-5 w-5 hover:text-zinc-900 cursor-pointer transition-colors" />
      </div>
      <div className="px-3 pb-3 text-[12px]">
        <p className="font-semibold text-zinc-900 dark:text-zinc-100 text-[11px] mb-0.5">0 likes</p>
        <p className="text-zinc-800 dark:text-zinc-200 leading-snug">
          <span className="font-semibold mr-1">{identity.handle}</span>
          {parts.map((p, i) => (
            <span key={i}>
              {p}
              {tags[i] && <span className="text-blue-500 font-medium">{tags[i]}</span>}
            </span>
          ))}
          {!content && <span className="text-zinc-400 italic">Start typing to preview...</span>}
        </p>
      </div>
    </div>
  )
}

function FacebookPreview({ content, media, identity }: PreviewProps) {
  return (
    <div className="rounded-xl border bg-white dark:bg-zinc-900 shadow-sm overflow-hidden text-[13px]">
      <div className="p-3">
        <div className="flex items-center gap-2 mb-3">
          <AccountAvatar
            identity={identity}
            className="w-9 h-9 rounded-full flex-shrink-0 text-[10px]"
            fallbackClass="bg-blue-100 dark:bg-blue-900 text-blue-700"
          />
          <div>
            <p className="font-semibold text-zinc-900 dark:text-zinc-100 leading-tight text-[12px]">{identity.name}</p>
            <p className="text-zinc-400 text-[10px] flex items-center gap-0.5">Just now · <Globe className="h-2.5 w-2.5" /></p>
          </div>
          <MoreHorizontal className="ml-auto h-4 w-4 text-zinc-400" />
        </div>
        <p className="whitespace-pre-wrap text-zinc-800 dark:text-zinc-200 leading-relaxed">
          {content || <span className="text-zinc-400 italic">Start typing to preview...</span>}
        </p>
      </div>
      {media.length > 0 && (
        <ObjectUrlImage file={media[0]} className="w-full aspect-video object-cover" />
      )}
      <div className="px-3 py-1.5 border-t flex text-zinc-500 text-[11px]">
        {[
          [<ThumbsUp key="l" className="h-3.5 w-3.5" />, 'Like'],
          [<MessageCircle key="c" className="h-3.5 w-3.5" />, 'Comment'],
          [<Share2 key="s" className="h-3.5 w-3.5" />, 'Share'],
        ].map(([icon, label]) => (
          <button key={String(label)} className="flex-1 flex items-center justify-center gap-1.5 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded-lg font-medium transition-colors">
            {icon}{label}
          </button>
        ))}
      </div>
    </div>
  )
}

function ThreadsPreview({ content, media, identity }: PreviewProps) {
  return (
    <div className="rounded-xl border bg-white dark:bg-zinc-900 shadow-sm p-4 text-[13px]">
      <div className="flex gap-3">
        <div className="flex flex-col items-center gap-1">
          <AccountAvatar
            identity={identity}
            className="w-9 h-9 rounded-full text-[10px]"
            fallbackClass="bg-zinc-200 dark:bg-zinc-700 text-zinc-600 dark:text-zinc-300"
          />
          <div className="w-0.5 flex-1 bg-zinc-200 dark:bg-zinc-700 rounded-full min-h-[20px]" />
        </div>
        <div className="flex-1 min-w-0 pb-3">
          <div className="flex items-center justify-between mb-1">
            <span className="font-semibold text-zinc-900 dark:text-zinc-100 text-[12px]">{identity.handle}</span>
            <div className="flex items-center gap-2 text-zinc-400">
              <span className="text-[11px]">just now</span>
              <MoreHorizontal className="h-4 w-4" />
            </div>
          </div>
          <p className="whitespace-pre-wrap text-zinc-800 dark:text-zinc-200 leading-relaxed">
            {content || <span className="text-zinc-400 italic">Start typing to preview...</span>}
          </p>
          {media.length > 0 && (
            <ObjectUrlImage file={media[0]} className="mt-2 w-full rounded-xl object-cover max-h-48" />
          )}
          <div className="mt-3 flex gap-4 text-zinc-500">
            <button className="hover:text-red-500 transition-colors"><Heart className="h-4 w-4" /></button>
            <button className="hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"><MessageCircle className="h-4 w-4" /></button>
            <button className="hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"><Repeat2 className="h-4 w-4" /></button>
            <button className="hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"><Share2 className="h-4 w-4" /></button>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2 mt-1">
        <div className="w-5 h-5 rounded-full bg-zinc-200 dark:bg-zinc-700 flex-shrink-0" />
        <span className="text-zinc-400 text-[11px]">Reply to {identity.handle}...</span>
      </div>
    </div>
  )
}

function TikTokPreview({ content, media, identity }: PreviewProps) {
  return (
    <div className="rounded-xl border bg-black text-white dark:bg-zinc-950 shadow-sm overflow-hidden text-[13px] flex flex-col h-[420px]">
      <div className="flex items-center justify-between px-3 py-2 text-white/80">
        <span className="font-semibold text-[12px]">For You</span>
        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-2.88 2.5 2.89 2.89 0 0 1-2.89-2.89 2.89 2.89 0 0 1 2.89-2.89c.28 0 .54.04.79.1V9.01a6.27 6.27 0 0 0-.79-.05 6.34 6.34 0 0 0-6.34 6.34 6.34 6.34 0 0 0 6.34 6.34 6.34 6.34 0 0 0 6.33-6.34V8.69a8.18 8.18 0 0 0 4.78 1.52V6.76a4.85 4.85 0 0 1-1.01-.07z"/></svg>
      </div>
      <div className="relative flex-1 bg-zinc-900 flex items-center justify-center">
        {media.length > 0
          ? <ObjectUrlImage file={media[0]} className="h-full w-full object-contain" />
          : <div className="text-center text-white/40"><PlaySquare className="h-10 w-10 mx-auto mb-2" />Upload a video or image to preview</div>
        }
      </div>
      <div className="p-3 space-y-2">
        <div className="flex items-center gap-2">
          <AccountAvatar
            identity={identity}
            className="w-8 h-8 rounded-full flex-shrink-0 text-[10px]"
            fallbackClass="bg-white text-black"
          />
          <span className="font-semibold text-[12px]">{identity.handle}</span>
        </div>
        <p className="whitespace-pre-wrap leading-relaxed text-zinc-100 text-[12px]">
          {content || <span className="text-white/40 italic">Start typing to preview...</span>}
        </p>
        <div className="flex items-center gap-4 text-white/70">
          <Heart className="h-4 w-4" />
          <MessageCircle className="h-4 w-4" />
          <Share2 className="h-4 w-4" />
        </div>
      </div>
    </div>
  )
}

const PreviewComponents: Record<string, React.ComponentType<PreviewProps>> = {
  linkedin: LinkedInPreview,
  twitter: TwitterPreview,
  instagram: InstagramPreview,
  facebook: FacebookPreview,
  threads: ThreadsPreview,
  tiktok: TikTokPreview,
}

export default function NewPostPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { data: accounts } = useAccounts()
  const createPostMutation = useCreatePost()
  const uploadMediaMutation = useUploadMedia()
  const generateContentMutation = useGenerateContent()
  const { setCtx } = useAdvisor()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [content, setContent] = useState('')
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([])
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([])
  const [previewPlatform, setPreviewPlatform] = useState<string>('')
  const [mediaFiles, setMediaFiles] = useState<File[]>([])
  const [mediaIds, setMediaIds] = useState<string[]>([])
  const [libraryAssets, setLibraryAssets] = useState<MediaAsset[]>([])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [scheduleDate, setScheduleDate] = useState('')
  const [tiktokPublishMode, setTiktokPublishMode] = useState<'MEDIA_UPLOAD' | 'DIRECT_POST'>('MEDIA_UPLOAD')
  const [tiktokPrivacyLevel, setTiktokPrivacyLevel] = useState('SELF_ONLY')
  const [aiGenerating, setAiGenerating] = useState(false)
  const [aiUsed, setAiUsed] = useState(false)
  const [providerInfo, setProviderInfo] = useState<{ provider?: string; fallback?: boolean; primary?: string } | null>(null)
  const [tone, setTone] = useState('professional')
  const [repurposing, setRepurposing] = useState(false)
  const [variants, setVariants] = useState<Record<string, string>>({})
  const [openVariants, setOpenVariants] = useState<Set<string>>(new Set())
  const [brandCompliance, setBrandCompliance] = useState<{ score: number; issues: Array<{ type: string; message: string; suggestion: string }>; banned_found: string[]; preferred_found: string[]; tone_match: { score: number; issues: string[]; suggestions: string[] } } | null>(null)
  const [complianceLoading, setComplianceLoading] = useState(false)
  const [musicAssetId, setMusicAssetId] = useState<string | null>(null)
  const [musicFileName, setMusicFileName] = useState<string>('')
  const [musicPickerOpen, setMusicPickerOpen] = useState(false)

  const connectedAccounts = useMemo(() => {
    const list = (accounts as SocialAccount[] | undefined) || []
    return list.filter((a) => !a.status || a.status === 'active')
  }, [accounts])
  const connectedPlatforms = useMemo(
    () => [...new Set(connectedAccounts.map((a) => a.platform as string))],
    [connectedAccounts]
  )

  // Prefill schedule from calendar links: /content/new?date=YYYY-MM-DD
  useEffect(() => {
    const date = searchParams.get('date')
    if (date && /^\d{4}-\d{2}-\d{2}$/.test(date) && !scheduleDate) {
      setScheduleDate(`${date}T19:00`)
    }
  }, [searchParams, scheduleDate])

  useEffect(() => {
    setCtx({ content, selectedPlatforms, hasMedia: mediaFiles.length > 0, aiUsed })
  }, [content, selectedPlatforms, mediaFiles, aiUsed, setCtx])

  // Auto-select all accounts on first load; for LinkedIn always use cloudless-gr org
  useEffect(() => {
    if (connectedAccounts.length === 0 || selectedPlatforms.length > 0) return
    const seen = new Set<string>()
    const toSelect: SocialAccount[] = []
    // LinkedIn: cloudless-gr org first
    const cloudless = connectedAccounts.find(
      (a) => a.platform === 'linkedin' && isOrgAccount(a)
    ) ?? connectedAccounts.find((a) => a.platform === 'linkedin')
    if (cloudless) { toSelect.push(cloudless); seen.add('linkedin') }
    // All other platforms: first connected account per platform
    for (const a of connectedAccounts) {
      if (!seen.has(a.platform)) { toSelect.push(a); seen.add(a.platform) }
    }
    if (toSelect.length > 0) {
      setSelectedPlatforms(toSelect.map((a) => a.platform))
      setSelectedAccountIds(toSelect.map((a) => a.id))
      setPreviewPlatform(cloudless ? 'linkedin' : toSelect[0].platform)
    }
  }, [connectedAccounts]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-select preview platform when selection changes
  useEffect(() => {
    if (selectedPlatforms.length > 0 && !selectedPlatforms.includes(previewPlatform)) {
      setPreviewPlatform(selectedPlatforms[0])
    }
    if (selectedPlatforms.length === 0) setPreviewPlatform('')
  }, [selectedPlatforms, previewPlatform])

  // Smart-default tone based on primary platform
  useEffect(() => {
    const defaults: Record<string, string> = {
      linkedin: 'professional',
      twitter: 'witty',
      instagram: 'inspirational',
      facebook: 'casual',
      threads: 'casual',
      tiktok: 'trendy',
    }
    const primary = selectedPlatforms[0]
    if (primary && defaults[primary]) setTone(defaults[primary])
  }, [selectedPlatforms[0]]) // eslint-disable-line react-hooks/exhaustive-deps

  const previewAccount = useMemo(() => {
    if (!previewPlatform) return null
    const selected = connectedAccounts.find(
      (a) => a.platform === previewPlatform && selectedAccountIds.includes(a.id)
    )
    return selected || preferredAccount(connectedAccounts, previewPlatform) || null
  }, [connectedAccounts, previewPlatform, selectedAccountIds])

  const previewIdentity = useMemo(
    () => (previewAccount ? identityFromAccount(previewAccount) : null),
    [previewAccount]
  )

  const overLimitPlatforms = useMemo(
    () =>
      selectedPlatforms.filter((pid) => {
        const meta = platforms.find((p) => p.id === pid)
        return meta ? content.length > meta.maxChars : false
      }),
    [selectedPlatforms, content.length]
  )

  const handlePlatformToggle = (platformId: string) => {
    if (!connectedPlatforms.includes(platformId)) {
      toast.error(`Connect your ${platformId} account first`)
      return
    }
    setSelectedPlatforms((prev) => {
      if (prev.includes(platformId)) {
        setSelectedAccountIds((ids) =>
          ids.filter((id) => {
            const acc = connectedAccounts.find((a) => a.id === id)
            return acc?.platform !== platformId
          })
        )
        return prev.filter((p) => p !== platformId)
      }
      const preferred = preferredAccount(connectedAccounts, platformId)
      if (preferred) {
        setSelectedAccountIds((ids) =>
          ids.includes(preferred.id) ? ids : [...ids, preferred.id]
        )
      }
      return [...prev, platformId]
    })
  }

  const handleAccountToggle = (account: SocialAccount) => {
    setSelectedAccountIds((prev) => {
      if (prev.includes(account.id)) {
        const next = prev.filter((id) => id !== account.id)
        const stillHasPlatform = next.some((id) => {
          const a = connectedAccounts.find((x) => x.id === id)
          return a?.platform === account.platform
        })
        if (!stillHasPlatform) {
          setSelectedPlatforms((plats) => plats.filter((p) => p !== account.platform))
        }
        return next
      }
      if (!selectedPlatforms.includes(account.platform)) {
        setSelectedPlatforms((plats) => [...plats, account.platform])
      }
      return [...prev, account.id]
    })
  }

  const handleMediaUpload = async (files: FileList) => {
    const newFiles = Array.from(files)
    if (mediaFiles.length + newFiles.length > 10) { toast.error('Maximum 10 media files'); return }
    for (const file of newFiles) {
      try {
        const result = await uploadMediaMutation.mutateAsync({ file, alt_text: '', tags: '' })
        const uploadedId =
          (result as { data?: { id?: string } })?.data?.id ||
          (result as { id?: string })?.id
        setMediaFiles((prev) => [...prev, file])
        if (uploadedId) setMediaIds((prev) => [...prev, String(uploadedId)])
      } catch { toast.error(`Failed to upload ${file.name}`) }
    }
  }

  const removeMediaAt = (index: number) => {
    setMediaFiles((prev) => prev.filter((_, i) => i !== index))
    setMediaIds((prev) => prev.filter((_, i) => i !== index))
  }

  const handleLibrarySelect = (assets: MediaAsset[]) => {
    const slots = 10 - mediaFiles.length - libraryAssets.length
    const picks = assets.slice(0, slots)
    setLibraryAssets((prev) => {
      const existingIds = new Set(prev.map((a) => a.id))
      return [...prev, ...picks.filter((a) => !existingIds.has(a.id))]
    })
    setMediaIds((prev) => {
      const existingIds = new Set(prev)
      return [...prev, ...picks.map((a) => a.id).filter((id) => !existingIds.has(id))]
    })
  }

  const removeLibraryAssetAt = (assetId: string) => {
    setLibraryAssets((prev) => prev.filter((a) => a.id !== assetId))
    setMediaIds((prev) => prev.filter((id) => id !== assetId))
  }

  const handleSubmit = async (e: React.FormEvent, action: 'draft' | 'publish' | 'schedule') => {
    e.preventDefault()
    if (!content.trim()) { toast.error('Please add some content'); return }
    if (selectedPlatforms.length === 0 || selectedAccountIds.length === 0) {
      toast.error('Select at least one platform')
      return
    }
    if (action === 'schedule' && !scheduleDate) { toast.error('Select a date and time'); return }
    if (action !== 'draft' && overLimitPlatforms.length > 0) {
      toast.error(`Content exceeds limit for ${overLimitPlatforms.join(', ')}`)
      return
    }
    try {
      const targets = selectedAccountIds.map((id) => ({ social_account_id: id }))
      const post = await createPostMutation.mutateAsync({
        content_text: content,
        media_ids: mediaIds.length ? mediaIds : undefined,
        music_asset_id: musicAssetId || undefined,
        targets,
        platform_specific: selectedPlatforms.includes('tiktok')
          ? { tiktok: { publish_mode: tiktokPublishMode, privacy_level: tiktokPrivacyLevel } }
          : undefined,
        scheduled_at: action === 'schedule' ? athensDateTimeLocalToIso(scheduleDate) : undefined,
      })
      if (action === 'publish') {
        const postId = (post as unknown as { data?: { id?: string } })?.data?.id
        if (postId) await contentApi.publishNow(postId)
        toast.success('Post published')
        router.push('/calendar')
      } else if (action === 'schedule') {
        toast.success('Post scheduled')
        router.push('/calendar')
      } else {
        toast.success('Draft saved')
        router.push('/content/new')
      }
    } catch { toast.error('Failed to create post') }
  }

  const handleAIGenerate = async () => {
    setAiGenerating(true)
    try {
      const platform = selectedPlatforms[0] || 'linkedin'
      const result = await generateContentMutation.mutateAsync({
        prompt: content || 'Write an engaging social media post',
        platform, tone, length: 'medium',
        include_hashtags: true, include_emojis: true,
      })
      const data = result.data
      const generated = data.content || ''
      const hashtags: string[] = data.hashtags || []
      setContent(hashtags.length ? `${generated}\n\n${hashtags.map((h: string) => `#${h}`).join(' ')}` : generated)
      setAiUsed(true)
      setProviderInfo({ provider: data._provider, fallback: data._fallback, primary: data._primary_provider })
      if (data.brand_compliance) {
        setBrandCompliance(data.brand_compliance)
      }
      toast.success('AI content generated')
    } catch { toast.error('Failed to generate content') }
    finally { setAiGenerating(false) }
  }

  const handleCheckCompliance = async () => {
    if (!content.trim() || content.trim().length < 10) { toast.error('Add more content first'); return }
    setComplianceLoading(true)
    try {
      const res = await brandApi.scoreCompliance({ content, platform: selectedPlatforms[0] || 'linkedin' })
      setBrandCompliance(res.data as typeof brandCompliance)
    } catch { toast.error('Failed to check brand compliance') }
    finally { setComplianceLoading(false) }
  }

  const handleTranscript = useCallback((text: string) => {
    const transcript = text.trim()
    if (!transcript) return
    setContent(prev => (prev.trim() ? `${prev.trim()}\n\n${transcript}` : transcript))
    setAiUsed(true)
    setOpenVariants(new Set())
  }, [])

  const handleRepurpose = async () => {
    if (!content.trim()) { toast.error('Add some content first'); return }
    if (selectedPlatforms.length === 0) { toast.error('Select at least one platform'); return }
    setRepurposing(true)
    setVariants({})
    setOpenVariants(new Set(selectedPlatforms))
    const platformTones: Record<string, string> = {
      linkedin: 'professional', twitter: 'witty', instagram: 'inspirational',
      facebook: 'casual', threads: 'casual', tiktok: 'trendy',
    }
    try {
      const results = await Promise.allSettled(
        selectedPlatforms.map(async (pid) => {
          const result = await aiApi.generateContent({
            prompt: `Adapt and repurpose this content for ${pid}, optimizing for its format and audience: ${content}`,
            platform: pid,
            tone: platformTones[pid] ?? tone,
            length: 'medium',
            include_hashtags: true,
            include_emojis: true,
          })
          const data = result.data
          const gen: string = data.content || ''
          const hashtags: string[] = data.hashtags || []
          return { pid, text: hashtags.length ? `${gen}\n\n${hashtags.map((h: string) => `#${h}`).join(' ')}` : gen }
        })
      )
      const newVariants: Record<string, string> = {}
      for (const r of results) {
        if (r.status === 'fulfilled') newVariants[r.value.pid] = r.value.text
      }
      setVariants(newVariants)
      const n = Object.keys(newVariants).length
      toast.success(`${n} platform variant${n !== 1 ? 's' : ''} generated`)
    } catch {
      toast.error('Failed to repurpose content')
    } finally {
      setRepurposing(false)
    }
  }

  const activePlatformMeta = platforms.find(p => p.id === previewPlatform)
  const PreviewComponent = previewPlatform ? PreviewComponents[previewPlatform] : null

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">New Post</h1>
        <p className="text-muted-foreground mt-1">Create and schedule content across platforms</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-6 items-start">
        {/* ── Left: Editor ───────────────────────────────── */}
        <div className="space-y-4">
          {/* Content type */}
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-base">Content Type</CardTitle></CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                {[
                  { label: 'Post',     icon: AlignLeft,    href: null,                      active: true,  desc: 'All' },
                  { label: 'Carousel', icon: LayoutTemplate, href: '/content/carousel/new', active: false, desc: 'LI · IG · TT' },
                  { label: 'Thread',   icon: List,          href: '/content/thread/new',    active: false, desc: 'X · Threads' },
                  { label: 'Poll',     icon: BarChart2,     href: '/content/poll/new',      active: false, desc: 'X · LI' },
                  { label: 'Story',    icon: PlaySquare,    href: '/content/story/new',     active: false, desc: 'IG · FB' },
                  { label: 'Article',  icon: BookOpen,      href: '/content/article/new',   active: false, desc: 'LI only' },
                ].map(({ label, icon: Icon, href, active, desc }) => (
                  <button
                    key={label}
                    type="button"
                    onClick={() => href && router.push(href)}
                    className={cn(
                      'flex flex-col items-center gap-1 rounded-lg border-2 p-3 text-xs font-medium transition-colors',
                      active ? 'border-primary bg-primary/10 text-primary' : 'border-muted bg-muted/30 text-muted-foreground hover:border-primary/50 hover:text-foreground'
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    <span>{label}</span>
                    <span className="text-[9px] opacity-50 leading-tight">{desc}</span>
                  </button>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Platform selector */}
          <Card data-tour="platform-selector">
            <CardHeader className="pb-3"><CardTitle className="text-base">Platforms</CardTitle></CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2">
                {platforms.map((platform) => {
                  const isConnected = connectedPlatforms.includes(platform.id)
                  const isSelected = selectedPlatforms.includes(platform.id)
                  return (
                    <button
                      key={platform.id}
                      onClick={() => handlePlatformToggle(platform.id)}
                      disabled={!isConnected}
                      className={cn(
                        'flex items-center gap-2 rounded-lg border-2 px-3 py-1.5 text-sm font-medium transition-all',
                        isSelected ? 'border-primary bg-primary text-primary-foreground shadow-sm' : 'border-muted hover:border-primary/50',
                        !isConnected && 'opacity-40 cursor-not-allowed'
                      )}
                    >
                      <span className={cn('w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-white', platform.color)}>
                        {platform.icon}
                      </span>
                      {platform.name}
                      {isSelected && previewPlatform !== platform.id && (
                        <CharRing count={content.length} max={platform.maxChars} size={20} />
                      )}
                    </button>
                  )
                })}
              </div>
              {selectedPlatforms.map((pid) => {
                const platformAccounts = connectedAccounts.filter((a) => a.platform === pid)
                if (platformAccounts.length === 0) return null
                return (
                  <div key={`accounts-${pid}`} className="mt-3 space-y-1.5">
                    <p className="text-xs text-muted-foreground">
                      Post as ({platforms.find((p) => p.id === pid)?.name}):
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {platformAccounts.map((account) => {
                        const active = selectedAccountIds.includes(account.id)
                        const label = account.display_name || account.username || account.id.slice(0, 8)
                        const isOrg = isOrgAccount(account)
                        return (
                          <button
                            key={account.id}
                            type="button"
                            onClick={() => handleAccountToggle(account)}
                            className={cn(
                              'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                              active
                                ? 'border-primary bg-primary/10 text-primary'
                                : 'border-muted text-muted-foreground hover:border-primary/40'
                            )}
                          >
                            {label}
                            {isOrg ? ' · Page' : ' · Personal'}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
              {selectedPlatforms.includes('tiktok') && (
                <div className="mt-4 space-y-1.5">
                  <label htmlFor="tiktok-publish-mode" className="text-xs font-medium">TikTok publishing</label>
                  <select
                    id="tiktok-publish-mode"
                    value={tiktokPublishMode}
                    onChange={(event) => setTiktokPublishMode(event.target.value as 'MEDIA_UPLOAD' | 'DIRECT_POST')}
                    className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                  >
                    <option value="MEDIA_UPLOAD">Upload draft — finish in TikTok inbox</option>
                    <option value="DIRECT_POST">Direct publish</option>
                  </select>
                  <p className="text-xs text-muted-foreground">
                    Upload draft uses video.upload. Direct publish requires video.publish approval.
                  </p>
                  {tiktokPublishMode === 'DIRECT_POST' && (
                    <select
                      aria-label="TikTok privacy"
                      value={tiktokPrivacyLevel}
                      onChange={(event) => setTiktokPrivacyLevel(event.target.value)}
                      className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                    >
                      <option value="SELF_ONLY">Only me</option>
                      <option value="MUTUAL_FOLLOW_FRIENDS">Friends</option>
                      <option value="FOLLOWER_OF_CREATOR">Followers</option>
                      <option value="PUBLIC_TO_EVERYONE">Everyone</option>
                    </select>
                  )}
                </div>
              )}
              {selectedPlatforms.length === 0 && (
                <p className="text-xs text-muted-foreground mt-2">
                  Need to connect accounts? <Link href="/accounts" className="text-primary hover:underline">Go to Accounts</Link>
                </p>
              )}
            </CardContent>
          </Card>

          {/* Content editor */}
          <Card data-tour="content-editor">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-base">Content</CardTitle>
                <div className="flex items-center gap-2">
                  {selectedPlatforms.length > 0 && content.trim().length > 0 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleRepurpose}
                      disabled={repurposing || aiGenerating}
                      className="gap-1.5"
                    >
                      {repurposing
                        ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        : <Layers className="h-3.5 w-3.5" />
                      }
                      {repurposing ? 'Repurposing…' : 'Repurpose'}
                    </Button>
                  )}
                  <VoiceRecorder
                    onTranscript={handleTranscript}
                    disabled={aiGenerating || repurposing}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleAIGenerate}
                    disabled={aiGenerating || repurposing}
                    data-tour="ai-assist"
                    className="gap-1.5"
                  >
                    <Sparkles className="h-3.5 w-3.5" />
                    {aiGenerating ? `Generating ${tone}…` : 'Generate'}
                  </Button>
                </div>
                {providerInfo?.provider && (
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground mt-1.5">
                    <span className="capitalize">{providerInfo.provider}</span>
                    {providerInfo.fallback && providerInfo.primary && (
                      <span className="text-amber-600 dark:text-amber-400">
                        (fallback from {providerInfo.primary})
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* Tone pills */}
              <div className="flex flex-wrap gap-1.5 mt-2">
                {([
                  { id: 'professional',  label: 'Professional', emoji: '💼' },
                  { id: 'casual',        label: 'Casual',       emoji: '😊' },
                  { id: 'witty',         label: 'Witty',        emoji: '😄' },
                  { id: 'inspirational', label: 'Inspirational',emoji: '✨' },
                  { id: 'educational',   label: 'Educational',  emoji: '📚' },
                  { id: 'trendy',        label: 'Trendy',       emoji: '🔥' },
                ] as const).map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => setTone(t.id)}
                    className={cn(
                      'flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-all',
                      tone === t.id
                        ? 'border-primary bg-primary text-primary-foreground shadow-sm'
                        : 'border-muted bg-muted/40 text-muted-foreground hover:border-primary/50 hover:text-foreground'
                    )}
                  >
                    <span>{t.emoji}</span>
                    {t.label}
                  </button>
                ))}
              </div>
            </CardHeader>
            <CardContent>
              <Textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="What do you want to share?"
                className="min-h-[200px] font-mono text-sm resize-none"
                rows={8}
              />

              <div className="mt-3">
                <SpellCheckButton text={content} onApply={setContent} disabled={aiGenerating || repurposing} />
              </div>

              {/* Per-platform char counts below editor */}
              {selectedPlatforms.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-3">
                  {selectedPlatforms.map((pid) => {
                    const p = platforms.find(pl => pl.id === pid)!
                    const pct = content.length / p.maxChars
                    const over = content.length > p.maxChars
                    return (
                      <button
                        key={pid}
                        onClick={() => setPreviewPlatform(pid)}
                        className={cn('flex items-center gap-1.5 text-xs rounded-md px-2 py-1 transition-colors', previewPlatform === pid ? 'bg-muted font-medium' : 'text-muted-foreground hover:text-foreground')}
                      >
                        <CharRing count={content.length} max={p.maxChars} size={24} />
                        <span>{p.name}</span>
                        {over && <Badge variant="destructive" className="text-[9px] px-1 py-0">over</Badge>}
                        {!over && pct > 0.8 && <span className="text-amber-500">{p.maxChars - content.length} left</span>}
                      </button>
                    )
                  })}
                </div>
              )}

              <div className="flex items-center justify-between mt-3">
                <div className="flex items-center gap-2">
                  <input ref={fileInputRef} type="file" accept="image/*,video/*" multiple className="hidden"
                    onChange={(e) => e.target.files && handleMediaUpload(e.target.files)} />
                  <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()} disabled={mediaFiles.length + libraryAssets.length >= 10}>
                    <ImageIcon className="mr-2 h-3.5 w-3.5" />
                    Upload {mediaFiles.length > 0 && `(${mediaFiles.length})`}
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)} disabled={mediaFiles.length + libraryAssets.length >= 10}>
                    <Layers className="mr-2 h-3.5 w-3.5" />
                    Library {libraryAssets.length > 0 && `(${libraryAssets.length})`}
                  </Button>
                  <MediaPickerDialog
                    open={pickerOpen}
                    onOpenChange={setPickerOpen}
                    onSelect={handleLibrarySelect}
                    multiple
                    title="Select from Media Library"
                  />
                </div>
                <span className="text-xs text-muted-foreground">{content.length} chars</span>
              </div>

              {(mediaFiles.length > 0 || libraryAssets.length > 0) && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {mediaFiles.map((file, index) => (
                    <div key={`${file.name}-${index}`} className="relative h-16 w-16 rounded-lg overflow-hidden border">
                      {file.type.startsWith('image/') && (
                        <ObjectUrlImage file={file} alt="Media preview" className="h-full w-full object-cover" />
                      )}
                      <button
                        type="button"
                        className="absolute top-0.5 right-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-black/60 text-white hover:bg-black/80"
                        onClick={() => removeMediaAt(index)}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                  {libraryAssets.map((asset) => (
                    <div key={asset.id} className="relative h-16 w-16 rounded-lg overflow-hidden border border-primary/40">
                      <SafeImage
                        src={mediaUrl(asset.storage_path)}
                        alt={asset.alt_text || asset.filename || ''}
                        className="h-full w-full object-cover"
                      />
                      <button
                        type="button"
                        className="absolute top-0.5 right-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-black/60 text-white hover:bg-black/80"
                        onClick={() => removeLibraryAssetAt(asset.id)}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Music / Audio Track */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2">
                <Music className="h-4 w-4 text-muted-foreground" />
                Music Track
              </CardTitle>
              <CardDescription>
                Add background music to video posts (TikTok, Instagram Reels). Audio is mixed into the video before publishing.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {musicAssetId ? (
                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <Music className="h-5 w-5 text-primary shrink-0" />
                    <span className="text-sm truncate">{musicFileName}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => { setMusicAssetId(null); setMusicFileName('') }}
                    >
                      <X className="h-4 w-4" /> Remove
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setMusicPickerOpen(true)}
                  >
                    <Layers className="mr-2 h-3.5 w-3.5" />
                    Pick from Library
                  </Button>
                  <MusicPickerDialog
                    open={musicPickerOpen}
                    onOpenChange={setMusicPickerOpen}
                    onSelect={(asset) => {
                      setMusicAssetId(String(asset.id))
                      setMusicFileName(asset.filename || asset.storage_path || 'Audio track')
                      setMusicPickerOpen(false)
                    }}
                    title="Select Music Track"
                  />
                </div>
              )}
              {!musicAssetId && (
                <p className="text-xs text-muted-foreground">
                  Upload audio files (MP3, WAV, AAC, M4A, OGG) to the media library first, then pick from here.
                </p>
              )}
            </CardContent>
          </Card>

          {/* Repurpose variants accordion */}
          {(repurposing || Object.keys(variants).length > 0) && selectedPlatforms.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base flex items-center gap-2">
                    <Layers className="h-4 w-4 text-muted-foreground" />
                    Platform Variants
                  </CardTitle>
                  {!repurposing && (
                    <button
                      type="button"
                      onClick={() => setVariants({})}
                      className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      Clear
                    </button>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-2 pt-0">
                {selectedPlatforms.map((pid) => {
                  const p = platforms.find(pl => pl.id === pid)!
                  const variantContent = variants[pid]
                  const isOpen = openVariants.has(pid)
                  return (
                    <div key={pid} className="rounded-lg border overflow-hidden">
                      <button
                        type="button"
                        onClick={() => setOpenVariants(prev => {
                          const next = new Set(prev)
                          if (next.has(pid)) next.delete(pid); else next.add(pid)
                          return next
                        })}
                        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-sm font-medium hover:bg-muted/50 transition-colors"
                      >
                        <span className={cn('w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-white flex-shrink-0', p.color)}>
                          {p.icon}
                        </span>
                        <span>{p.name}</span>
                        {repurposing && !variantContent && (
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                        )}
                        {variantContent && (
                          <span className="text-xs text-muted-foreground font-normal">{variantContent.length} chars</span>
                        )}
                        <ChevronDown className={cn('ml-auto h-4 w-4 text-muted-foreground transition-transform duration-200', isOpen && 'rotate-180')} />
                      </button>
                      {isOpen && (
                        <div className="border-t px-3 py-3 space-y-3 bg-muted/20">
                          {repurposing && !variantContent ? (
                            <div className="flex items-center gap-2 text-sm text-muted-foreground py-1">
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              Generating {p.name} variant…
                            </div>
                          ) : variantContent ? (
                            <>
                              <p className="text-sm whitespace-pre-wrap text-foreground leading-relaxed">{variantContent}</p>
                              <div className="flex gap-2">
                                <Button
                                  size="sm"
                                  onClick={() => {
                                    setContent(variantContent)
                                    setPreviewPlatform(pid)
                                    toast.success(`Using ${p.name} variant`)
                                  }}
                                >
                                  Use this
                                </Button>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => {
                                    navigator.clipboard.writeText(variantContent)
                                    toast.success('Copied to clipboard')
                                  }}
                                >
                                  <Copy className="mr-1.5 h-3.5 w-3.5" />
                                  Copy
                                </Button>
                              </div>
                            </>
                          ) : (
                            <p className="text-sm text-muted-foreground">Generation failed for this platform.</p>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </CardContent>
            </Card>
          )}

          {/* SEO Analysis */}
          {content.trim().length > 20 && (
            <SeoPanel
              content={content}
              platform={selectedPlatforms[0] || 'linkedin'}
            />
          )}

          {/* Brand Compliance */}
          {content.trim().length > 20 && (
            <Card>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base flex items-center gap-2">
                    <Shield className="h-4 w-4" />
                    Brand Compliance
                  </CardTitle>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleCheckCompliance}
                    disabled={complianceLoading}
                  >
                    {complianceLoading ? 'Checking...' : 'Check Compliance'}
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {brandCompliance ? (
                  <div className="space-y-3">
                    <div className="flex items-center gap-3">
                      <div className={`text-2xl font-bold ${brandCompliance.score >= 4 ? 'text-green-600' : brandCompliance.score >= 3 ? 'text-yellow-600' : 'text-red-600'}`}>
                        {brandCompliance.score}/5
                      </div>
                      <div className="text-sm text-muted-foreground">
                        {brandCompliance.score >= 4 ? 'On-brand' : brandCompliance.score >= 3 ? 'Needs improvement' : 'Off-brand'}
                        {brandCompliance.tone_match && typeof brandCompliance.tone_match.score === 'number' && (
                          <span className="ml-2">· Tone match: {brandCompliance.tone_match.score}/5</span>
                        )}
                      </div>
                    </div>
                    {brandCompliance.banned_found && brandCompliance.banned_found.length > 0 && (
                      <div className="text-sm">
                        <span className="text-red-600 font-medium">Banned phrases found: </span>
                        <span className="text-red-700">{brandCompliance.banned_found.join(', ')}</span>
                      </div>
                    )}
                    {brandCompliance.preferred_found && brandCompliance.preferred_found.length > 0 && (
                      <div className="text-sm">
                        <span className="text-green-600 font-medium">Preferred phrases used: </span>
                        <span className="text-green-700">{brandCompliance.preferred_found.join(', ')}</span>
                      </div>
                    )}
                    {brandCompliance.issues && brandCompliance.issues.length > 0 && (
                      <div className="space-y-1">
                        {brandCompliance.issues.map((issue, i) => (
                          <div key={i} className="text-sm rounded-lg border p-2">
                            <div className="text-foreground">{issue.message}</div>
                            {issue.suggestion && <div className="text-muted-foreground text-xs mt-1">{issue.suggestion}</div>}
                          </div>
                        ))}
                      </div>
                    )}
                    {brandCompliance.tone_match && brandCompliance.tone_match.suggestions && brandCompliance.tone_match.suggestions.length > 0 && (
                      <div className="space-y-1">
                        <div className="text-sm font-medium">Suggestions:</div>
                        {brandCompliance.tone_match.suggestions.map((s, i) => (
                          <div key={i} className="text-sm text-muted-foreground">{s}</div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Click &quot;Check Compliance&quot; to score your content against your brand guidelines, or generate content with AI for automatic scoring.
                  </p>
                )}
              </CardContent>
            </Card>
          )}

          {/* Actions */}
          <div className="flex flex-col sm:flex-row gap-3 justify-end border-t pt-4" data-tour="publish-actions">
            {scheduleDate && (
              <p className="sm:mr-auto text-xs text-muted-foreground self-center">
                Schedule preset: <span className="font-medium text-foreground">{scheduleDate.replace('T', ' ')} Athens</span>
              </p>
            )}
            <Button variant="outline" onClick={(e) => handleSubmit(e, 'draft')}>
              <Save className="mr-2 h-4 w-4" />Save Draft
            </Button>
            <Dialog>
              <DialogTrigger asChild>
                <Button variant="outline"><Calendar className="mr-2 h-4 w-4" />Schedule</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader><DialogTitle>Schedule Post</DialogTitle></DialogHeader>
                <div className="py-4">
                  <label className="block text-sm font-medium mb-2">Schedule for (Europe/Athens)</label>
                  <Input type="datetime-local" value={scheduleDate} onChange={(e) => setScheduleDate(e.target.value)} min={toAthensDateTimeLocal(new Date())} />
                </div>
                <DialogFooter>
                  <DialogClose asChild>
                    <Button variant="outline">Cancel</Button>
                  </DialogClose>
                  <Button onClick={(e) => handleSubmit(e, 'schedule')}>Schedule</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            <Button onClick={(e) => handleSubmit(e, 'publish')}>
              <Send className="mr-2 h-4 w-4" />Publish Now
            </Button>
          </div>
        </div>

        {/* ── Right: Live Preview ─────────────────────────── */}
        <div className="lg:sticky lg:top-20 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Live Preview</h2>
            {selectedPlatforms.length > 1 && (
              <div className="flex gap-1">
                {selectedPlatforms.map((pid) => {
                  const p = platforms.find(pl => pl.id === pid)!
                  return (
                    <button
                      key={pid}
                      onClick={() => setPreviewPlatform(pid)}
                      className={cn(
                        'w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white transition-all',
                        p.color,
                        previewPlatform === pid ? 'ring-2 ring-offset-1 ring-foreground scale-110' : 'opacity-60 hover:opacity-100'
                      )}
                      title={p.name}
                    >
                      {p.icon}
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {PreviewComponent && activePlatformMeta && previewIdentity ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs text-muted-foreground px-1">
                <span className="font-medium">
                  {activePlatformMeta.name}
                  {` · ${previewIdentity.name}`}
                </span>
                <div className="flex items-center gap-2">
                  <CharRing count={content.length} max={activePlatformMeta.maxChars} size={28} />
                  <span className={cn(content.length > activePlatformMeta.maxChars ? 'text-destructive font-medium' : '')}>
                    {content.length > activePlatformMeta.maxChars
                      ? `${content.length - activePlatformMeta.maxChars} over limit`
                      : `${activePlatformMeta.maxChars - content.length} remaining`
                    }
                  </span>
                </div>
              </div>
              <PreviewComponent content={content} media={mediaFiles} identity={previewIdentity} />
            </div>
          ) : (
            <div className="rounded-xl border border-dashed bg-muted/20 p-10 text-center">
              <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
                <ImageIcon className="h-6 w-6 text-muted-foreground" />
              </div>
              <p className="text-sm font-medium text-muted-foreground">Select a platform to see a live preview</p>
              <p className="text-xs text-muted-foreground mt-1">Your post will appear here as you type</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
