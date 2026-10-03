'use client'

import { useState, useEffect, useCallback } from 'react'
import {
  FileText,
  Play,
  RefreshCw,
  Loader2,
  Eye,
  X,
  FlaskConical,
  CalendarDays,
  Paperclip,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Textarea } from '@/components/ui/Textarea'
import { Switch } from '@/components/ui/Switch'
import { EmptyState } from '@/components/ui/EmptyState'
import { reportsApi, type ReportNotebook, type ReportRun } from '@/services/api'
import toast from 'react-hot-toast'
import { format } from 'date-fns'
import { formatErrorToast } from '@/lib/humanizeError'

function fmtDate(iso: string) {
  try {
    return format(new Date(iso), 'dd MMM yyyy HH:mm')
  } catch {
    return iso
  }
}

export default function ReportsPage() {
  const [notebooks, setNotebooks] = useState<ReportNotebook[]>([])
  const [reports, setReports] = useState<ReportRun[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState<string | null>(null)
  const [viewer, setViewer] = useState<{ title: string; html: string } | null>(null)
  const [viewerLoading, setViewerLoading] = useState(false)
  const [runPanel, setRunPanel] = useState<string | null>(null)
  const [paramsText, setParamsText] = useState('')
  const [sendEmail, setSendEmail] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [nb, rep] = await Promise.all([reportsApi.listNotebooks(), reportsApi.list({ limit: 100 })])
      setNotebooks(nb.data.notebooks)
      setReports(rep.data.reports)
    } catch (err) {
      toast.error(formatErrorToast('Couldn’t load the reports. Please try again.', err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const openReport = async (rep: ReportRun) => {
    if (!rep.html_file) return
    setViewerLoading(true)
    try {
      const res = await reportsApi.getFile(rep.html_file)
      setViewer({ title: rep.subject || rep.run, html: res.data })
    } catch (err) {
      toast.error(formatErrorToast('Couldn’t open that report. Please try again.', err))
    } finally {
      setViewerLoading(false)
    }
  }

  const runNotebook = async (name: string) => {
    let parameters: Record<string, unknown> = {}
    const text = paramsText.trim()
    if (text) {
      try {
        parameters = JSON.parse(text)
      } catch {
        // key=value per line fallback
        for (const line of text.split('\n')) {
          const idx = line.indexOf('=')
          if (idx > 0) parameters[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
        }
      }
    }
    setRunning(name)
    try {
      const res = await reportsApi.run(name, parameters, sendEmail)
      toast.success(`Report queued (task ${res.data.task_id.slice(0, 8)}…)`)
      setRunPanel(null)
      // Reports take a while — refresh the list after a delay.
      setTimeout(load, 30_000)
      setTimeout(load, 90_000)
    } catch (err) {
      toast.error(formatErrorToast('Couldn’t queue the report run. Please try again.', err))
    } finally {
      setRunning(null)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Reports</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Notebook-generated reports — strategy briefs, LinkedIn ads digests. View rendered output or run one now.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          <span className="ml-2">Refresh</span>
        </Button>
      </div>

      {/* ── Run a report ─────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <FlaskConical className="h-4 w-4" /> Run a report
          </CardTitle>
          <CardDescription>
            Executes the notebook on a worker and renders output below. Email is off by default — enable it to also receive the report by mail.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {notebooks.length === 0 && !loading ? (
            <p className="text-sm text-muted-foreground">No report notebooks found.</p>
          ) : (
            notebooks.map((nb) => (
              <div key={nb.name} className="rounded-lg border p-3 space-y-3">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div className="flex items-center gap-2 min-w-0">
                    <FileText className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                    <span className="font-medium text-sm">{nb.name}</span>
                    <span className="text-xs text-muted-foreground">updated {fmtDate(nb.modified)}</span>
                  </div>
                  <Button
                    size="sm"
                    variant={runPanel === nb.name ? 'secondary' : 'default'}
                    onClick={() => setRunPanel(runPanel === nb.name ? null : nb.name)}
                  >
                    <Play className="h-3.5 w-3.5 mr-1.5" /> Run
                  </Button>
                </div>
                {runPanel === nb.name && (
                  <div className="space-y-3 border-t pt-3">
                    <div className="space-y-1.5">
                      <Label htmlFor={`params-${nb.name}`} className="text-xs">
                        Parameters (JSON or key=value per line, optional)
                      </Label>
                      <Textarea
                        id={`params-${nb.name}`}
                        rows={3}
                        placeholder={'{"insight_days": 7}'}
                        value={paramsText}
                        onChange={(e) => setParamsText(e.target.value)}
                        className="font-mono text-xs"
                      />
                    </div>
                    <div className="flex items-center justify-between gap-4 flex-wrap">
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <Switch checked={sendEmail} onCheckedChange={setSendEmail} />
                        Also send by email
                      </label>
                      <Button size="sm" onClick={() => runNotebook(nb.name)} disabled={running === nb.name}>
                        {running === nb.name ? (
                          <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                        ) : (
                          <Play className="h-3.5 w-3.5 mr-1.5" />
                        )}
                        Queue run
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {/* ── Past runs ────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Generated reports</CardTitle>
          <CardDescription>Newest first. Open the rendered HTML or grab the files.</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground py-6 justify-center">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading reports…
            </div>
          ) : reports.length === 0 ? (
            <EmptyState
              icon={FileText}
              title="No reports yet"
              description="Run a notebook above, or wait for the scheduled daily brief."
            />
          ) : (
            <div className="divide-y">
              {reports.map((rep) => (
                <div key={`${rep.run}-${rep.html_file || 'x'}`} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium truncate">{rep.subject || rep.run}</p>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      <Badge variant="secondary" className="text-xs">{rep.notebook}</Badge>
                      <span className="text-xs text-muted-foreground flex items-center gap-1">
                        <CalendarDays className="h-3 w-3" /> {fmtDate(rep.created)}
                      </span>
                      {rep.files.length > 2 && (
                        <span className="text-xs text-muted-foreground flex items-center gap-1">
                          <Paperclip className="h-3 w-3" /> {rep.files.length - 2} files
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {rep.html_file && (
                      <Button size="sm" variant="outline" onClick={() => openReport(rep)} disabled={viewerLoading}>
                        <Eye className="h-3.5 w-3.5 mr-1.5" /> View
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Viewer overlay ───────────────────────────────────────────── */}
      {viewer && (
        <div className="fixed inset-0 z-50 bg-background/95 flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b">
            <h2 className="text-sm font-medium truncate">{viewer.title}</h2>
            <Button variant="ghost" size="icon" onClick={() => setViewer(null)} aria-label="Close report viewer">
              <X className="h-4 w-4" />
            </Button>
          </div>
          <iframe
            title={viewer.title}
            sandbox="allow-same-origin"
            srcDoc={viewer.html}
            className="flex-1 w-full bg-white"
          />
        </div>
      )}
    </div>
  )
}
