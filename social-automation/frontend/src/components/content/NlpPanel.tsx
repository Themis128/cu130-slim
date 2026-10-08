'use client'

import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Brain, Loader2, AlertTriangle, CheckCircle2 } from 'lucide-react'
import { aiApi } from '@/services/api'
import { cn } from '@/lib/utils'

type NlpResult = {
  score: number
  avg_sentence_words: number
  issue_count: number
  issues: { field: string; reason: string; snippet: string; matches: string[] }[]
  recommendations: string[]
}

const REASON_LABELS: Record<string, string> = {
  jargon_or_buzzwords: 'Jargon / buzzwords',
  long_sentences: 'Sentences too long',
  long_uncommon_words: 'Uncommon long words',
  weak_hook: 'Weak hook',
  generic_hook: 'Generic hook opener',
  ai_tell_phrases: 'AI-tell phrases',
  em_dash: 'Em dash',
  flat_rhythm: 'Flat sentence rhythm',
  no_specifics: 'No specifics',
  missing_cta: 'No CTA / question',
  missing_ps: 'Missing P.S.',
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

export function NlpPanel({
  content,
  platform = 'linkedin',
}: {
  content: string
  platform?: string
}) {
  const [result, setResult] = useState<NlpResult | null>(null)
  const [pending, setPending] = useState(false)

  // The check is deterministic (no AI call) — cheap enough to run live,
  // debounced so it doesn't fire per keystroke.
  useEffect(() => {
    const trimmed = content.trim()
    if (trimmed.length <= 20) {
      setResult(null)
      return
    }
    let cancelled = false
    setPending(true)
    const timer = setTimeout(async () => {
      try {
        const res = await aiApi.nlpCheck({ content: trimmed, platform })
        if (!cancelled) setResult(res.data)
      } catch {
        // keep last known score on transient failure
      } finally {
        if (!cancelled) setPending(false)
      }
    }, 800)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [content, platform])

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <Brain className="h-4 w-4 text-muted-foreground" />
            NLP Score
          </CardTitle>
          {pending && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {!result && !pending && (
          <p className="text-sm text-muted-foreground py-2">
            Write more than 20 characters to get a live plain-English score (Sofia Kakkava rules: hook,
            jargon, rhythm, specifics, CTA).
          </p>
        )}

        {result && (
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
                <p className="text-sm font-medium">Plain-English / Copy Quality</p>
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
                <p className="text-[10px] text-muted-foreground mt-1">
                  ~{result.avg_sentence_words} words/sentence · {result.issue_count}{' '}
                  {result.issue_count === 1 ? 'issue' : 'issues'}
                </p>
              </div>
            </div>

            {/* Issues */}
            {result.issues.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {result.issues.map((issue, i) => (
                  <Badge key={`${issue.reason}-${i}`} variant="secondary" className="text-xs">
                    {REASON_LABELS[issue.reason] ?? issue.reason}
                  </Badge>
                ))}
              </div>
            )}

            {/* Recommendations */}
            {result.recommendations.length > 0 && (
              <ul className="space-y-1 pt-1">
                {result.recommendations.map((rec, i) => (
                  <li key={i} className="text-xs text-muted-foreground flex items-start gap-1.5">
                    <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0 text-amber-500" />
                    {rec}
                  </li>
                ))}
              </ul>
            )}

            {/* Quality indicator */}
            <div className="flex items-center gap-1.5 pt-1 border-t">
              {result.score >= 80 ? (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                  <span className="text-xs text-muted-foreground">
                    Copy passes the plain-English playbook for {platform}
                  </span>
                </>
              ) : (
                <>
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                  <span className="text-xs text-muted-foreground">
                    Tighten the hook, cut jargon, add specifics
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
