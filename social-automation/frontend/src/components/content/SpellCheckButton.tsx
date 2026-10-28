'use client'

import { useState } from 'react'
import { CheckSquare, Loader2, X, ChevronDown, ChevronUp } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { aiApi } from '@/services/api'
import toast from 'react-hot-toast'

type Match = {
  message: string
  offset: number
  length: number
  replacements: string[]
  rule_id: string
  context: string
}

interface SpellCheckButtonProps {
  text: string
  onApply: (corrected: string) => void
  language?: string
  disabled?: boolean
}

export function SpellCheckButton({ text, onApply, language = 'en-US', disabled }: SpellCheckButtonProps) {
  const [loading, setLoading] = useState(false)
  const [matches, setMatches] = useState<Match[] | null>(null)
  const [open, setOpen] = useState(false)

  const runCheck = async () => {
    if (!text.trim()) { toast.error('Nothing to check'); return }
    setLoading(true)
    try {
      const res = await aiApi.spellcheck(text, language)
      const m = res.data.matches
      setMatches(m)
      setOpen(m.length > 0)
      if (m.length === 0) toast.success('No spelling or grammar issues found')
    } catch {
      toast.error('Spell check unavailable — start LanguageTool first')
    } finally {
      setLoading(false)
    }
  }

  const applyFix = (match: Match, replacement: string) => {
    const corrected =
      text.slice(0, match.offset) + replacement + text.slice(match.offset + match.length)
    onApply(corrected)
    const diff = replacement.length - match.length
    setMatches(prev =>
      (prev ?? [])
        .filter(m => m !== match)
        .map(m => m.offset > match.offset ? { ...m, offset: m.offset + diff } : m)
    )
    toast.success('Correction applied')
  }

  const dismiss = () => { setMatches(null); setOpen(false) }

  return (
    <div className="w-full">
      {/* Button row */}
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={runCheck}
          disabled={disabled || loading || !text.trim()}
          className="gap-1.5"
        >
          {loading
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <CheckSquare className="h-3.5 w-3.5" />}
          {loading ? 'Checking…' : matches !== null ? 'Re-check' : 'Spell Check'}
        </Button>

        {matches !== null && matches.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen(o => !o)}
            className="flex items-center gap-1 text-xs font-medium text-amber-600 dark:text-amber-400 hover:underline"
          >
            {matches.length} issue{matches.length !== 1 ? 's' : ''}
            {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
        )}

        {matches !== null && matches.length === 0 && (
          <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
            ✓ No issues
          </span>
        )}

        {matches !== null && (
          <button
            type="button"
            onClick={dismiss}
            className="ml-auto text-muted-foreground hover:text-foreground transition-colors"
            title="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* Results panel */}
      {open && matches && matches.length > 0 && (
        <div className="mt-2 rounded-lg border divide-y overflow-hidden">
          {matches.map((m, i) => {
            const flagged = text.slice(m.offset, m.offset + m.length)
            return (
              <div key={i} className="px-3 py-2.5 space-y-1.5 bg-amber-50/60 dark:bg-amber-950/20">
                <div className="flex items-start gap-2">
                  <code className="shrink-0 text-xs font-mono bg-amber-100 dark:bg-amber-900/60 px-1.5 py-0.5 rounded text-amber-800 dark:text-amber-300">
                    {flagged || '—'}
                  </code>
                  <p className="text-xs text-muted-foreground leading-snug">{m.message}</p>
                </div>
                {m.replacements.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 items-center">
                    <span className="text-xs text-muted-foreground">→</span>
                    {m.replacements.slice(0, 5).map(r => (
                      <button
                        key={r}
                        type="button"
                        onClick={() => applyFix(m, r)}
                        className="text-xs px-2 py-0.5 rounded-full border border-border hover:bg-primary/10 hover:border-primary/40 hover:text-primary font-medium transition-colors"
                      >
                        {r}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
