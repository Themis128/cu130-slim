'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Loader2,
  RefreshCw,
  Video,
  XCircle,
} from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { AxiosError } from 'axios'

import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { accountsApi, browserApi, tiktokApi } from '@/services/api'
import type { SocialAccount } from '@/types'

function errMsg(err: unknown, fallback = 'Request failed'): string {
  const ax = err as AxiosError<{ detail?: string }>
  return ax?.response?.data?.detail || ax?.message || fallback
}

export default function TikTokManagePage() {
  const qc = useQueryClient()
  const [accountId, setAccountId] = useState('')
  const [publishId, setPublishId] = useState('')
  const [videoCursor, setVideoCursor] = useState(0)

  const { data: accountsRes, isLoading: loadingAccounts } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const tiktokAccounts: SocialAccount[] = (accountsRes?.data || []).filter(
    (a: SocialAccount) => a.platform === 'tiktok'
  )

  useEffect(() => {
    if (!accountId && tiktokAccounts.length) {
      setAccountId(tiktokAccounts[0].id)
    }
  }, [accountId, tiktokAccounts])

  const healthQ = useQuery({
    queryKey: ['tiktok-health', accountId],
    queryFn: () => tiktokApi.health(accountId).then((r) => r.data),
    enabled: !!accountId,
    refetchInterval: 60_000,
  })

  const videosQ = useQuery({
    queryKey: ['tiktok-videos', accountId, videoCursor],
    queryFn: () =>
      tiktokApi.listVideos(accountId, { cursor: videoCursor || undefined, max_count: 20 }).then((r) => r.data),
    enabled: !!accountId && !!healthQ.data?.has_video_list_scope,
  })

  const uploadsQ = useQuery({
    queryKey: ['tiktok-uploads', accountId],
    queryFn: () => tiktokApi.listUploads(accountId, { hours: 24, live_status: true }).then((r) => r.data),
    enabled: !!accountId,
  })

  const refreshMut = useMutation({
    mutationFn: () => accountsApi.refresh(accountId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tiktok-health', accountId] })
      qc.invalidateQueries({ queryKey: ['accounts'] })
    },
  })

  const validateMut = useMutation({
    mutationFn: () => accountsApi.validate(accountId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tiktok-health', accountId] }),
  })

  const statusMut = useMutation({
    mutationFn: (id: string) => tiktokApi.publishStatus(accountId, id).then((r) => r.data),
  })

  const cancelMut = useMutation({
    mutationFn: (id: string) => tiktokApi.publishCancel(accountId, id).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tiktok-uploads', accountId] }),
  })

  const sessionQ = useQuery({
    queryKey: ['tiktok-sidecar-session'],
    queryFn: () => browserApi.checkTiktokSession().then((r) => r.data),
    enabled: !!accountId,
  })

  const health = healthQ.data
  const pendingCount = (uploadsQ.data || []).filter((u: { is_pending?: boolean }) => u.is_pending).length

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Video className="h-6 w-6" />
          TikTok
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Manage Login Kit + Content Posting + Display API — creator info, videos, publish status, and cancel.
          Docs:{' '}
          <a
            className="underline"
            href="https://developers.tiktok.com/doc/content-posting-api-get-started"
            target="_blank"
            rel="noreferrer"
          >
            Content Posting API
          </a>
        </p>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">TikTok account</CardTitle>
          <CardDescription>Connected via OAuth scopes: user.info.basic, video.publish, video.upload, video.list</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {loadingAccounts ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading…
            </div>
          ) : tiktokAccounts.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No TikTok account connected. Connect from{' '}
              <Link href="/accounts" className="underline">Channels</Link>.
            </p>
          ) : (
            <select
              className="w-full max-w-md rounded-md border bg-background px-3 py-2 text-sm"
              value={accountId}
              onChange={(e) => {
                setAccountId(e.target.value)
                setVideoCursor(0)
              }}
            >
              {tiktokAccounts.map((a) => (
                <option key={a.id} value={a.id}>
                  @{a.username || a.display_name || a.id} ({a.status})
                </option>
              ))}
            </select>
          )}
          {accountId && (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => healthQ.refetch()} disabled={healthQ.isFetching}>
                {healthQ.isFetching ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />}
                Refresh health
              </Button>
              <Button size="sm" variant="outline" onClick={() => validateMut.mutate()} disabled={validateMut.isPending}>
                Validate token
              </Button>
              <Button size="sm" variant="outline" onClick={() => refreshMut.mutate()} disabled={refreshMut.isPending}>
                Refresh OAuth token
              </Button>
              <Button size="sm" variant="outline" asChild>
                <Link href="/accounts">Reconnect (add scopes)</Link>
              </Button>
              <Button size="sm" variant="outline" asChild>
                <Link href="/browser-login">Browser session</Link>
              </Button>
            </div>
          )}
          {(refreshMut.isError || validateMut.isError) && (
            <p className="text-sm text-destructive">{errMsg(refreshMut.error || validateMut.error)}</p>
          )}
        </CardContent>
      </Card>

      {accountId && (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Token</CardTitle></CardHeader>
              <CardContent className="text-sm space-y-1">
                <div className="flex items-center gap-2">
                  {health?.token_valid ? <CheckCircle2 className="h-4 w-4 text-green-500" /> : <XCircle className="h-4 w-4 text-destructive" />}
                  {health?.token_valid ? 'Valid' : 'Invalid / expired'}
                </div>
                <p className="text-muted-foreground text-xs">
                  Expires: {health?.token_expires_at ? new Date(health.token_expires_at).toLocaleString() : '—'}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Scopes</CardTitle></CardHeader>
              <CardContent className="flex flex-wrap gap-1">
                {(health?.scopes || []).map((s: string) => (
                  <Badge key={s} variant="secondary" className="text-[10px]">{s}</Badge>
                ))}
                {!health?.has_video_list_scope && (
                  <p className="text-xs text-amber-600 w-full mt-1">Missing video.list — reconnect TikTok</p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Pending uploads (24h)</CardTitle></CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">{pendingCount}</p>
                <p className="text-xs text-muted-foreground">TikTok limits ~5 pending inbox shares / 24h</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Browser sidecar</CardTitle></CardHeader>
              <CardContent className="text-sm">
                {sessionQ.isLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <span>{sessionQ.data?.logged_in ? 'Session active' : 'No session — use Visual Login'}</span>
                )}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Creator info</CardTitle>
              <CardDescription>
                Official POST /v2/post/publish/creator_info/query/ — privacy options & max duration for DIRECT_POST UX
              </CardDescription>
            </CardHeader>
            <CardContent>
              {healthQ.isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : health?.creator ? (
                <div className="flex gap-4 items-start">
                  {health.creator.creator_avatar_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={health.creator.creator_avatar_url} alt="" className="h-14 w-14 rounded-full" />
                  )}
                  <div className="text-sm space-y-1">
                    <p className="font-medium">
                      @{health.creator.creator_username} · {health.creator.creator_nickname}
                    </p>
                    <p className="text-muted-foreground">
                      Max video: {health.creator.max_video_post_duration_sec ?? '—'}s
                    </p>
                    <div className="flex flex-wrap gap-1">
                      {(health.creator.privacy_level_options || []).map((p: string) => (
                        <Badge key={p} variant="outline" className="text-[10px]">{p}</Badge>
                      ))}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      comments {health.creator.comment_disabled ? 'off' : 'on'} · duet{' '}
                      {health.creator.duet_disabled ? 'off' : 'on'} · stitch{' '}
                      {health.creator.stitch_disabled ? 'off' : 'on'}
                    </p>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground flex items-center gap-2">
                  <AlertCircle className="h-4 w-4" /> Creator info unavailable (need video.publish + valid token)
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Videos</CardTitle>
              <CardDescription>
                Official Display API POST /v2/video/list/ (scope video.list, max 20/page)
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {!health?.has_video_list_scope ? (
                <p className="text-sm text-amber-600">Reconnect TikTok to grant video.list</p>
              ) : videosQ.isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : videosQ.isError ? (
                <p className="text-sm text-destructive">{errMsg(videosQ.error)}</p>
              ) : (
                <>
                  <div className="divide-y rounded-md border">
                    {(videosQ.data?.videos || []).length === 0 && (
                      <p className="p-3 text-sm text-muted-foreground">No public videos returned</p>
                    )}
                    {(videosQ.data?.videos || []).map((v: {
                      id: string
                      title?: string
                      view_count?: number
                      like_count?: number
                      share_url?: string
                      cover_image_url?: string
                    }) => (
                      <div key={v.id} className="flex items-center gap-3 p-3 text-sm">
                        {v.cover_image_url ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={v.cover_image_url} alt="" className="h-12 w-12 rounded object-cover" />
                        ) : (
                          <div className="h-12 w-12 rounded bg-muted" />
                        )}
                        <div className="flex-1 min-w-0">
                          <p className="font-medium truncate">{v.title || v.id}</p>
                          <p className="text-xs text-muted-foreground">
                            {v.view_count ?? 0} views · {v.like_count ?? 0} likes
                          </p>
                        </div>
                        {v.share_url && (
                          <a href={v.share_url} target="_blank" rel="noreferrer" className="text-muted-foreground hover:text-foreground">
                            <ExternalLink className="h-4 w-4" />
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={!videosQ.data?.has_more}
                      onClick={() => setVideoCursor(videosQ.data?.cursor || 0)}
                    >
                      Next page
                    </Button>
                    {videoCursor > 0 && (
                      <Button size="sm" variant="ghost" onClick={() => setVideoCursor(0)}>Reset</Button>
                    )}
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Uploads & cancel</CardTitle>
              <CardDescription>
                Status: POST /v2/post/publish/status/fetch/ · Cancel: POST /v2/post/publish/cancel/
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-wrap gap-2 items-end">
                <div className="flex-1 min-w-[220px]">
                  <label className="text-xs text-muted-foreground">publish_id</label>
                  <Input
                    value={publishId}
                    onChange={(e) => setPublishId(e.target.value)}
                    placeholder="v_inbox_file~v2.…"
                  />
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!publishId || statusMut.isPending}
                  onClick={() => statusMut.mutate(publishId)}
                >
                  Check status
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={!publishId || cancelMut.isPending}
                  onClick={() => cancelMut.mutate(publishId)}
                >
                  Cancel upload
                </Button>
              </div>
              {statusMut.data && (
                <pre className="text-xs bg-muted p-3 rounded overflow-auto">{JSON.stringify(statusMut.data, null, 2)}</pre>
              )}
              {(statusMut.isError || cancelMut.isError) && (
                <p className="text-sm text-destructive">{errMsg(statusMut.error || cancelMut.error)}</p>
              )}
              {cancelMut.data && (
                <p className="text-sm">Cancel result: {cancelMut.data.error_code || JSON.stringify(cancelMut.data)}</p>
              )}

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium">Recent SocialAuto TikTok publish_ids (24h)</p>
                  <Button size="sm" variant="ghost" onClick={() => uploadsQ.refetch()}>
                    <RefreshCw className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {uploadsQ.isLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (uploadsQ.data || []).length === 0 ? (
                  <p className="text-sm text-muted-foreground">No recent TikTok uploads tracked</p>
                ) : (
                  <div className="divide-y rounded-md border text-sm">
                    {(uploadsQ.data || []).map((u: {
                      publish_id: string
                      status?: string
                      fail_reason?: string
                      is_pending?: boolean
                      post_id?: string
                    }) => (
                      <div key={u.publish_id} className="flex flex-wrap items-center gap-2 p-3">
                        <code className="text-xs break-all flex-1">{u.publish_id}</code>
                        <Badge variant={u.is_pending ? 'outline' : 'secondary'}>{u.status || '—'}</Badge>
                        <Button size="sm" variant="ghost" onClick={() => { setPublishId(u.publish_id); statusMut.mutate(u.publish_id) }}>
                          Status
                        </Button>
                        {u.is_pending && (
                          <Button size="sm" variant="outline" onClick={() => cancelMut.mutate(u.publish_id)}>
                            Cancel
                          </Button>
                        )}
                        {u.fail_reason && <span className="text-xs text-destructive w-full">{u.fail_reason}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
