'use client'

import { useState, useEffect, useCallback } from 'react'
import {
  Instagram, Facebook, Linkedin, Twitter, Youtube,
  Monitor, RefreshCw, CheckCircle2, AlertCircle, Loader2,
  StopCircle, Download, ExternalLink,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { browserApi } from '@/services/api'

function TikTokIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
      <path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-2.88 2.5 2.89 2.89 0 0 1-2.89-2.89 2.89 2.89 0 0 1 2.89-2.89c.28 0 .54.04.79.1V9.01a6.27 6.27 0 0 0-.79-.05 6.34 6.34 0 0 0-6.34 6.34 6.34 6.34 0 0 0 6.34 6.34 6.34 6.34 0 0 0 6.33-6.34V8.69a8.18 8.18 0 0 0 4.78 1.52V6.76a4.85 4.85 0 0 1-1.01-.07z"/>
    </svg>
  )
}

const PLATFORM_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  instagram: Instagram,
  facebook: Facebook,
  linkedin: Linkedin,
  twitter: Twitter,
  tiktok: TikTokIcon,
  youtube: Youtube,
}

const PLATFORM_LABELS: Record<string, string> = {
  instagram: 'Instagram',
  facebook: 'Facebook',
  linkedin: 'LinkedIn',
  tiktok: 'TikTok',
  twitter: 'Twitter / X',
  threads: 'Threads',
  reddit: 'Reddit',
  youtube: 'YouTube',
  pinterest: 'Pinterest',
  tumblr: 'Tumblr',
  medium: 'Medium',
  discord: 'Discord',
  telegram: 'Telegram',
  whatsapp: 'WhatsApp',
}

interface SessionStatus {
  platform: string | null
  status: string
  message: string
  cookies_found: string[]
}

export default function BrowserLoginPage() {
  const [platforms, setPlatforms] = useState<Record<string, { url: string; cookies: string[] }>>({})
  const [novncUrl, setNovncUrl] = useState('')
  const [session, setSession] = useState<SessionStatus | null>(null)
  const [starting, setStarting] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [importResult, setImportResult] = useState<{ success: boolean; message: string } | null>(null)
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    browserApi.getPlatforms().then(({ data }) => setPlatforms(data)).catch(() => {})
    browserApi.getNovncUrl().then(({ data }) => setNovncUrl(data.url)).catch(() => {})
  }, [])

  const pollStatus = useCallback(async () => {
    try {
      const { data } = await browserApi.getStatus()
      setSession(data)
    } catch {}
  }, [])

  useEffect(() => {
    if (session?.status === 'waiting' || session?.status === 'extracting') {
      const interval = setInterval(pollStatus, 3000)
      return () => clearInterval(interval)
    }
  }, [session?.status, pollStatus])

  const handleStart = async (platform: string) => {
    setStarting(platform)
    setError('')
    setImportResult(null)
    try {
      const { data } = await browserApi.startSession(platform)
      setSession({ platform: data.platform, status: data.status, message: data.message, cookies_found: [] })
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Failed to start browser session')
    } finally {
      setStarting(null)
    }
  }

  const handleStop = async () => {
    try {
      await browserApi.stopSession()
      setSession(null)
    } catch {}
  }

  const handleImportInstagram = async () => {
    setImporting(true)
    setImportResult(null)
    try {
      const { data } = await browserApi.importInstagramSession()
      setImportResult({ success: data.success, message: data.message })
    } catch (e: any) {
      setImportResult({ success: false, message: e.response?.data?.detail || 'Import failed' })
    } finally {
      setImporting(false)
    }
  }

  const handleImportTiktok = async () => {
    setImporting(true)
    setImportResult(null)
    try {
      // Extract cookies from the browser session
      const { data: cookieData } = await browserApi.getCookies()
      const sessionId = cookieData.cookies?.find((c: any) => c.name === 'sessionid')?.value
      if (!sessionId) {
        setImportResult({ success: false, message: 'No sessionid cookie found. Make sure you are logged into TikTok.' })
        return
      }
      const { data } = await browserApi.importTiktokSession(sessionId)
      setImportResult({
        success: data.logged_in ?? data.status === 'ok',
        message: data.logged_in
          ? `TikTok session imported — logged in as ${data.title || 'unknown'}`
          : 'Session imported but login could not be verified',
      })
    } catch (e: any) {
      setImportResult({ success: false, message: e.response?.data?.detail || 'Import failed' })
    } finally {
      setImporting(false)
    }
  }

  const statusColor = (status: string) => {
    if (status === 'done') return 'text-green-500'
    if (status === 'error') return 'text-red-500'
    if (status === 'waiting' || status === 'extracting') return 'text-blue-500'
    return 'text-muted-foreground'
  }

  const StatusIcon = ({ status }: { status: string }) => {
    if (status === 'done') return <CheckCircle2 className="h-5 w-5 text-green-500" />
    if (status === 'error') return <AlertCircle className="h-5 w-5 text-red-500" />
    if (status === 'waiting' || status === 'extracting') return <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
    return <Monitor className="h-5 w-5 text-muted-foreground" />
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Visual Browser Login</h1>
        <p className="text-muted-foreground mt-1">
          Log into any social platform through a real browser you can watch and control.
          Cookies are extracted automatically after login.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/50 bg-red-500/10 p-3 text-sm text-red-500">
          {error}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Left: Platform selector + session status */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Monitor className="h-5 w-5" />
                Select Platform
              </CardTitle>
              <CardDescription>
                Click a platform to open its login page in the browser.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-3">
                {Object.keys(PLATFORM_LABELS).map((platform) => {
                  const Icon = PLATFORM_ICONS[platform] || Monitor
                  const isActive = session?.platform === platform
                  const isStarting = starting === platform
                  return (
                    <button
                      key={platform}
                      onClick={() => handleStart(platform)}
                      disabled={!!starting || (session?.status === 'waiting' && !isActive)}
                      className={`flex flex-col items-center gap-2 rounded-lg border p-4 transition hover:bg-accent ${
                        isActive ? 'border-primary bg-primary/5' : ''
                      } disabled:opacity-50 disabled:cursor-not-allowed`}
                    >
                      {isStarting ? (
                        <Loader2 className="h-6 w-6 animate-spin" />
                      ) : (
                        <Icon className="h-6 w-6" />
                      )}
                      <span className="text-xs font-medium">{PLATFORM_LABELS[platform]}</span>
                    </button>
                  )
                })}
              </div>
            </CardContent>
          </Card>

          {session && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <StatusIcon status={session.status} />
                  Session Status
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Platform</span>
                  <span className="font-medium">{PLATFORM_LABELS[session.platform || ''] || session.platform}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Status</span>
                  <span className={`font-medium ${statusColor(session.status)}`}>{session.status}</span>
                </div>
                <div className="text-sm text-muted-foreground">{session.message}</div>
                {session.cookies_found.length > 0 && (
                  <div>
                    <div className="text-sm text-muted-foreground mb-1">Cookies found:</div>
                    <div className="flex flex-wrap gap-1">
                      {session.cookies_found.map((c) => (
                        <span key={c} className="rounded bg-green-500/10 px-2 py-0.5 text-xs text-green-500">
                          {c}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                <div className="flex gap-2 pt-2">
                  {session.status === 'done' && session.platform === 'instagram' && (
                    <Button onClick={handleImportInstagram} disabled={importing} size="sm">
                      {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                      Import to Sidecar
                    </Button>
                  )}
                  {session.status === 'done' && session.platform === 'tiktok' && (
                    <Button onClick={handleImportTiktok} disabled={importing} size="sm">
                      {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                      Import to TikTok Sidecar
                    </Button>
                  )}
                  <Button onClick={handleStop} variant="outline" size="sm">
                    <StopCircle className="h-4 w-4" />
                    Stop Session
                  </Button>
                  <Button onClick={pollStatus} variant="ghost" size="sm">
                    <RefreshCw className="h-4 w-4" />
                    Refresh
                  </Button>
                </div>
                {importResult && (
                  <div className={`rounded p-2 text-sm ${importResult.success ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-500'}`}>
                    {importResult.message}
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        {/* Right: noVNC iframe */}
        <div>
          <Card className="h-full">
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span className="flex items-center gap-2">
                  <Monitor className="h-5 w-5" />
                  Browser Viewer
                </span>
                {novncUrl && (
                  <a href={novncUrl} target="_blank" rel="noopener noreferrer">
                    <ExternalLink className="h-4 w-4" />
                  </a>
                )}
              </CardTitle>
              <CardDescription>
                Watch and interact with the browser here. Log in to the selected platform.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {novncUrl ? (
                <iframe
                  src={novncUrl}
                  className="w-full rounded-lg border"
                  style={{ height: '500px', minHeight: '400px' }}
                  title="Browser Viewer"
                />
              ) : (
                <div className="flex h-[400px] items-center justify-center text-muted-foreground">
                  <div className="text-center">
                    <Monitor className="mx-auto h-12 w-12 mb-2 opacity-50" />
                    <p>Browser viewer not available</p>
                    <p className="text-xs mt-1">Make sure the browser-novnc container is running.</p>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
