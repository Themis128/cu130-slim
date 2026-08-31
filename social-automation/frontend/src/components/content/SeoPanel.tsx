'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Search, Loader2, TrendingUp, Hash, Link as LinkIcon, FileText, CheckCircle2, AlertTriangle, Globe } from 'lucide-react'
import { useAnalyzeSeo } from '@/hooks/useQueries'
import { cn } from '@/lib/utils'

type SeoResult = {
  platform: string
  score: number
  keywords: string[]
  meta: { title: string; description: string }
  open_graph: {
    og_title: string
    og_description: string
    og_type: string
    twitter_card: string
    twitter_title: string
    twitter_description: string
  }
  character_count: number
  hashtag_count: number
  link_count: number
}

function scoreColor(score: number): string {
  if (score >= 80) return 'text-green-600'
  if (score >= 60) return 'text-amber-500'
  return 'text-red-500'
}

function scoreBg(score: number): string {
  if (score >= 80) return 'bg-green-500'
  if (score >= 60) return 'bg-amber-500'
  return 'bg-red-500'
}

export function SeoPanel({
  content,
  platform = 'linkedin',
  onApplyMeta,
}: {
  content: string
  platform?: string
  onApplyMeta?: (meta: { title: string; description: string }) => void
}) {
  const [result, setResult] = useState<SeoResult | null>(null)
  const analyzeSeo = useAnalyzeSeo()

  const handleAnalyze = async () => {
    if (!content.trim()) return
    try {
      const res = await analyzeSeo.mutateAsync({ content, platform })
      setResult(res.data as SeoResult)
    } catch {
      // error toast handled by the hook
    }
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <Search className="h-4 w-4 text-muted-foreground" />
            SEO Analysis
          </CardTitle>
          <Button
            size="sm"
            variant="outline"
            onClick={handleAnalyze}
            disabled={!content.trim() || analyzeSeo.isPending}
          >
            {analyzeSeo.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Search className="mr-1.5 h-3.5 w-3.5" />
            )}
            Analyze
          </Button>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {!result && !analyzeSeo.isPending && (
          <p className="text-sm text-muted-foreground py-2">
            Click &ldquo;Analyze&rdquo; to get an SEO score, keyword suggestions, and meta tags for this content.
          </p>
        )}

        {analyzeSeo.isPending && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Analyzing content…
          </div>
        )}

        {result && !analyzeSeo.isPending && (
          <div className="space-y-3 pt-1">
            {/* Score */}
            <div className="flex items-center gap-3">
              <div className="relative flex items-center justify-center w-12 h-12">
                <svg width={48} height={48} className="-rotate-90">
                  <circle cx={24} cy={24} r={20} fill="none" stroke="currentColor" strokeWidth={4} className="text-muted/20" />
                  <circle
                    cx={24}
                    cy={24}
                    r={20}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={4}
                    strokeDasharray={2 * Math.PI * 20}
                    strokeDashoffset={2 * Math.PI * 20 * (1 - result.score / 100)}
                    className={scoreColor(result.score)}
                    strokeLinecap="round"
                  />
                </svg>
                <span className={cn('absolute text-sm font-bold', scoreColor(result.score))}>
                  {result.score}
                </span>
              </div>
              <div className="flex-1">
                <p className="text-sm font-medium">Content Score</p>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <div className="h-1.5 flex-1 rounded-full bg-muted overflow-hidden">
                    <div
                      className={cn('h-full rounded-full transition-all', scoreBg(result.score))}
                      style={{ width: `${result.score}%` }}
                    />
                  </div>
                  <span className={cn('text-xs font-medium', scoreColor(result.score))}>
                    {result.score >= 80 ? 'Good' : result.score >= 60 ? 'Fair' : 'Needs work'}
                  </span>
                </div>
              </div>
            </div>

            {/* Quick stats */}
            <div className="grid grid-cols-3 gap-2">
              <div className="flex items-center gap-1.5 rounded-md bg-muted/40 px-2 py-1.5">
                <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                <div>
                  <p className="text-[10px] text-muted-foreground leading-none">Characters</p>
                  <p className="text-xs font-medium leading-tight">{result.character_count}</p>
                </div>
              </div>
              <div className="flex items-center gap-1.5 rounded-md bg-muted/40 px-2 py-1.5">
                <Hash className="h-3.5 w-3.5 text-muted-foreground" />
                <div>
                  <p className="text-[10px] text-muted-foreground leading-none">Hashtags</p>
                  <p className="text-xs font-medium leading-tight">{result.hashtag_count}</p>
                </div>
              </div>
              <div className="flex items-center gap-1.5 rounded-md bg-muted/40 px-2 py-1.5">
                <LinkIcon className="h-3.5 w-3.5 text-muted-foreground" />
                <div>
                  <p className="text-[10px] text-muted-foreground leading-none">Links</p>
                  <p className="text-xs font-medium leading-tight">{result.link_count}</p>
                </div>
              </div>
            </div>

            {/* Keywords */}
            {result.keywords.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1.5 flex items-center gap-1">
                  <TrendingUp className="h-3 w-3" />
                  Top Keywords
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {result.keywords.slice(0, 10).map((kw, i) => (
                    <Badge key={`${kw}-${i}`} variant="secondary" className="text-xs">
                      {kw}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {/* Meta tags */}
            {(result.meta?.title || result.meta?.description) && (
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <p className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                    <Search className="h-3 w-3" />
                    Suggested Meta Tags
                  </p>
                  {onApplyMeta && (result.meta.title || result.meta.description) && (
                    <button
                      onClick={() => onApplyMeta(result.meta)}
                      className="text-xs text-primary hover:underline flex items-center gap-1"
                    >
                      <CheckCircle2 className="h-3 w-3" />
                      Apply
                    </button>
                  )}
                </div>
                {result.meta.title && (
                  <div className="rounded-md border bg-muted/20 px-2.5 py-1.5 mb-1.5">
                    <p className="text-[10px] text-muted-foreground mb-0.5">Title</p>
                    <p className="text-xs font-medium leading-snug">{result.meta.title}</p>
                  </div>
                )}
                {result.meta.description && (
                  <div className="rounded-md border bg-muted/20 px-2.5 py-1.5">
                    <p className="text-[10px] text-muted-foreground mb-0.5">Description</p>
                    <p className="text-xs leading-snug text-muted-foreground">{result.meta.description}</p>
                  </div>
                )}
              </div>
            )}

            {/* Open Graph preview */}
            {result.open_graph?.og_title && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1.5 flex items-center gap-1">
                  <Globe className="h-3 w-3" />
                  Open Graph / Twitter Card Preview
                </p>
                <div className="rounded-lg border overflow-hidden bg-white">
                  <div className="bg-muted/30 h-24 flex items-center justify-center">
                    <span className="text-xs text-muted-foreground">og:image preview</span>
                  </div>
                  <div className="px-3 py-2">
                    <p className="text-xs font-medium text-foreground leading-snug truncate">
                      {result.open_graph.og_title}
                    </p>
                    <p className="text-[11px] text-muted-foreground leading-snug line-clamp-2 mt-0.5">
                      {result.open_graph.og_description}
                    </p>
                    <p className="text-[10px] text-muted-foreground/60 mt-1">
                      {result.open_graph.twitter_card}
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* Quality indicator */}
            <div className="flex items-center gap-1.5 pt-1 border-t">
              {result.score >= 70 ? (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                  <span className="text-xs text-muted-foreground">
                    Content meets quality guidelines for {result.platform}
                  </span>
                </>
              ) : (
                <>
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                  <span className="text-xs text-muted-foreground">
                    Consider adding more keywords, hashtags, or improving readability
                  </span>
                </>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
