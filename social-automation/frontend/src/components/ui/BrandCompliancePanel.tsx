'use client'

import { useState } from 'react'
import { Shield, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import { Button } from './Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './Card'
import { Badge } from './Badge'
import { brandApi } from '@/services/api'

interface BrandCompliancePanelProps {
  content: string
  platform?: string
  onScoreChange?: (score: number) => void
}

interface ComplianceResult {
  score: number
  issues: Array<{ type: string; message: string; suggestion?: string }>
  banned_found: string[]
  preferred_found: string[]
  tone_match: number
}

export function BrandCompliancePanel({ content, platform, onScoreChange }: BrandCompliancePanelProps) {
  const [result, setResult] = useState<ComplianceResult | null>(null)
  const [loading, setLoading] = useState(false)

  const checkCompliance = async () => {
    if (!content.trim()) return
    setLoading(true)
    try {
      const res = await brandApi.scoreCompliance({ content, platform })
      const data = res.data as ComplianceResult
      setResult(data)
      onScoreChange?.(data.score)
    } catch {
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  const scoreColor = (score: number) => {
    if (score >= 4) return 'text-green-600'
    if (score >= 3) return 'text-yellow-600'
    return 'text-red-600'
  }

  const scoreLabel = (score: number) => {
    if (score >= 4) return 'On Brand'
    if (score >= 3) return 'Needs Work'
    return 'Off Brand'
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Shield className="h-5 w-5" />
          Brand Compliance
        </CardTitle>
        <CardDescription>Check if your content aligns with your brand voice and guidelines</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Button variant="outline" onClick={checkCompliance} disabled={loading || !content.trim()}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Shield className="h-4 w-4" />}
          Check Compliance
        </Button>

        {result && (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <span className={`text-3xl font-bold ${scoreColor(result.score)}`}>{result.score}/5</span>
              <Badge variant={result.score >= 4 ? 'default' : result.score >= 3 ? 'secondary' : 'destructive'}>
                {result.score >= 4 ? <CheckCircle2 className="h-3 w-3 mr-1" /> : <AlertCircle className="h-3 w-3 mr-1" />}
                {scoreLabel(result.score)}
              </Badge>
              {result.tone_match > 0 && (
                <span className="text-sm text-muted-foreground">Tone match: {result.tone_match}/5</span>
              )}
            </div>

            {result.banned_found.length > 0 && (
              <div>
                <p className="text-sm font-medium text-destructive mb-1">Banned phrases found:</p>
                <div className="flex flex-wrap gap-2">
                  {result.banned_found.map((p, i) => <Badge key={i} variant="destructive">{p}</Badge>)}
                </div>
              </div>
            )}

            {result.preferred_found.length > 0 && (
              <div>
                <p className="text-sm font-medium text-green-600 mb-1">Preferred phrases used:</p>
                <div className="flex flex-wrap gap-2">
                  {result.preferred_found.map((p, i) => <Badge key={i} variant="secondary">{p}</Badge>)}
                </div>
              </div>
            )}

            {result.issues.length > 0 && (
              <div className="space-y-2">
                <p className="text-sm font-medium">Issues:</p>
                {result.issues.map((issue, i) => (
                  <div key={i} className="rounded-md border p-3 text-sm">
                    <div className="flex items-center gap-2 mb-1">
                      <AlertCircle className="h-4 w-4 text-yellow-600" />
                      <span className="font-medium">{issue.type}</span>
                    </div>
                    <p className="text-muted-foreground">{issue.message}</p>
                    {issue.suggestion && (
                      <p className="text-green-600 mt-1">Suggestion: {issue.suggestion}</p>
                    )}
                  </div>
                ))}
              </div>
            )}

            {result.issues.length === 0 && result.banned_found.length === 0 && result.score >= 4 && (
              <div className="flex items-center gap-2 text-sm text-green-600">
                <CheckCircle2 className="h-4 w-4" />
                Content is on-brand and ready to publish.
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
