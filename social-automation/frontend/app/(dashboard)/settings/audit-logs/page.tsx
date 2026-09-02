'use client'

import { useState } from 'react'
import { Shield, Activity, Filter } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import { useAuditLogs } from '@/hooks/useQueries'

const ACTION_COLORS: Record<string, string> = {
  delete: 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300',
  publish: 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300',
  approve: 'bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-300',
  reject: 'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300',
  submit_review: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
  create: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300',
  update: 'bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300',
}

export default function AuditLogsPage() {
  const [actionFilter, setActionFilter] = useState<string | undefined>(undefined)
  const [page, setPage] = useState(1)

  const { data, isLoading } = useAuditLogs({ action: actionFilter, page, page_size: 50 })

  const entries = (data as { entries?: Array<{ id: string; user_email: string | null; action: string; resource_type: string; resource_id: string | null; detail: string | null; created_at: string }> } | undefined)?.entries ?? []
  const total = (data as { total?: number } | undefined)?.total ?? 0

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <Shield className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Audit Logs</h1>
          <p className="text-sm text-muted-foreground">Track all actions across your team</p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <Filter className="h-4 w-4 text-muted-foreground" />
        <Button
          variant={actionFilter === undefined ? 'default' : 'outline'}
          size="sm"
          onClick={() => { setActionFilter(undefined); setPage(1) }}
        >
          All
        </Button>
        {['create', 'update', 'delete', 'publish', 'approve', 'reject', 'submit_review'].map((a) => (
          <Button
            key={a}
            variant={actionFilter === a ? 'default' : 'outline'}
            size="sm"
            onClick={() => { setActionFilter(a); setPage(1) }}
            className="capitalize"
          >
            {a.replace('_', ' ')}
          </Button>
        ))}
      </div>

      {/* Log table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Activity className="h-4 w-4" />
            {total} entr{total !== 1 ? 'ies' : 'y'}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
            </div>
          ) : entries.length === 0 ? (
            <p className="text-center text-sm text-muted-foreground py-8">No audit log entries found.</p>
          ) : (
            <div className="space-y-1">
              {entries.map((e) => (
                <div key={e.id} className="flex items-start gap-3 rounded-lg border p-3 hover:bg-muted/30 transition-colors">
                  <Badge className={`capitalize text-xs ${ACTION_COLORS[e.action] ?? 'bg-gray-100 text-gray-700'}`}>
                    {e.action.replace('_', ' ')}
                  </Badge>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-medium">{e.user_email ?? 'Unknown'}</span>
                      <span className="text-muted-foreground">·</span>
                      <span className="text-muted-foreground">{e.resource_type}</span>
                      {e.resource_id && (
                        <span className="text-xs text-muted-foreground font-mono">{e.resource_id.slice(0, 8)}</span>
                      )}
                    </div>
                    {e.detail && <p className="text-xs text-muted-foreground mt-0.5 truncate">{e.detail}</p>}
                  </div>
                  <span className="text-xs text-muted-foreground flex-shrink-0">{new Date(e.created_at).toLocaleString()}</span>
                </div>
              ))}
            </div>
          )}
          {total > 50 && (
            <div className="flex items-center justify-between mt-4">
              <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage(p => p - 1)}>Previous</Button>
              <span className="text-sm text-muted-foreground">Page {page}</span>
              <Button variant="outline" size="sm" disabled={entries.length < 50} onClick={() => setPage(p => p + 1)}>Next</Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
