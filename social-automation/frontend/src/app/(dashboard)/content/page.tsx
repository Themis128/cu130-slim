'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, Filter, Plus, MoreVertical, Edit, Trash2, Calendar, Send, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from '@/components/ui/DropdownMenu'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from '@/components/ui/Dialog'
import { usePosts, useDeletePost, usePublishPost, useSchedulePost } from '@/hooks/useQueries'
import { formatRelativeTime } from '@/lib/utils'
import Link from 'next/link'
import { format } from 'date-fns'
import toast from 'react-hot-toast'

const statusOptions = [
  { value: '', label: 'All Status' },
  { value: 'draft', label: 'Draft' },
  { value: 'scheduled', label: 'Scheduled' },
  { value: 'published', label: 'Published' },
  { value: 'failed', label: 'Failed' },
]

const platformOptions = [
  { value: '', label: 'All Platforms' },
  { value: 'linkedin', label: 'LinkedIn' },
  { value: 'twitter', label: 'Twitter/X' },
  { value: 'instagram', label: 'Instagram' },
  { value: 'facebook', label: 'Facebook' },
  { value: 'threads', label: 'Threads' },
]

export function ContentPage() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [platformFilter, setPlatformFilter] = useState('')
  const [page, setPage] = useState(1)
  const [selectedPost, setSelectedPost] = useState<{ id: string; scheduled_at?: string } | null>(null)
  const [scheduleDate, setScheduleDate] = useState('')

  const { data, isLoading } = usePosts({ status: statusFilter, page, page_size: 20 })
  const deleteMutation = useDeletePost()
  const publishMutation = usePublishPost()
  const scheduleMutation = useSchedulePost()

  const posts = data?.items || []
  const totalPages = data?.pages || 1

  const handlePublish = async (id: string) => {
    try {
      await publishMutation.mutateAsync(id)
    } catch (error) {
      toast.error('Failed to publish')
    }
  }

  const handleSchedule = async (id: string, date: string) => {
    try {
      await scheduleMutation.mutateAsync({ id, scheduled_at: date })
      setSelectedPost(null)
      setScheduleDate('')
    } catch (error) {
      toast.error('Failed to schedule')
    }
  }

  const handleDelete = async (id: string) => {
    if (confirm('Are you sure you want to delete this post?')) {
      try {
        await deleteMutation.mutateAsync(id)
      } catch (error) {
        toast.error('Failed to delete')
      }
    }
  }

  const openScheduleDialog = (post: { id: string; scheduled_at?: string }) => {
    setSelectedPost(post)
    setScheduleDate(post.scheduled_at ? new Date(post.scheduled_at).toISOString().slice(0, 16) : '')
  }

  const statusBadge = (status: string) => {
    const variants: Record<string, 'default' | 'success' | 'warning' | 'destructive' | 'secondary'> = {
      draft: 'secondary',
      scheduled: 'warning',
      publishing: 'default',
      published: 'success',
      failed: 'destructive',
      archived: 'default',
    }
    return <Badge variant={variants[status] || 'default'}>{status}</Badge>
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Content</h1>
          <p className="text-muted-foreground mt-1">Manage your social media posts</p>
        </div>
        <Button asChild>
          <Link href="/content/new">
            <Plus className="mr-2 h-4 w-4" />
            New Post
          </Link>
        </Button>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-col sm:flex-row gap-4">
            <div className="relative flex-1 max-w-sm">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search posts..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-10"
              />
            </div>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                {statusOptions.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={platformFilter} onValueChange={setPlatformFilter}>
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="Platform" />
              </SelectTrigger>
              <SelectContent>
                {platformOptions.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {/* Posts Table */}
      <Card>
        <CardContent className="pt-0">
          {isLoading ? (
            <div className="p-8">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Content</TableHead>
                    <TableHead>Platform</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Schedule</TableHead>
                    <TableHead>Updated</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[1, 2, 3, 4, 5].map((i) => (
                    <TableRow key={i}>
                      <TableCell><div className="h-4 w-3/4 bg-muted rounded animate-pulse" /></TableCell>
                      <TableCell><div className="h-4 w-20 bg-muted rounded animate-pulse" /></TableCell>
                      <TableCell><div className="h-4 w-20 bg-muted rounded animate-pulse" /></TableCell>
                      <TableCell><div className="h-4 w-24 bg-muted rounded animate-pulse" /></TableCell>
                      <TableCell><div className="h-4 w-24 bg-muted rounded animate-pulse" /></TableCell>
                      <TableCell className="text-right"><div className="h-4 w-16 bg-muted rounded animate-pulse" /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : posts.length === 0 ? (
            <div className="py-12 text-center">
              <p className="text-muted-foreground">No posts found</p>
              <Button asChild className="mt-4">
                <Link href="/content/new">Create your first post</Link>
              </Button>
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Content</TableHead>
                    <TableHead>Platform</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Schedule</TableHead>
                    <TableHead>Updated</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {posts.map((post) => (
                    <TableRow key={post.id}>
                      <TableCell className="max-w-[300px]">
                        <p className="font-medium line-clamp-1">{post.content?.slice(0, 100) || 'No content'}</p>
                        {post.media_urls && post.media_urls.length > 0 && (
                          <p className="text-xs text-muted-foreground">{post.media_urls.length} media attachment(s)</p>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="capitalize">{post.platform}</Badge>
                      </TableCell>
                      <TableCell>{statusBadge(post.status)}</TableCell>
                      <TableCell>
                        {post.scheduled_at ? (
                          <span className="flex items-center gap-1 text-sm">
                            <Calendar className="h-3.5 w-3.5" />
                            {format(new Date(post.scheduled_at), 'MMM d, yyyy HH:mm')}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>{formatRelativeTime(post.updated_at)}</TableCell>
                      <TableCell className="text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon">
                              <MoreVertical className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem asChild>
                              <Link href={`/content/${post.id}/edit`}>
                                <Edit className="mr-2 h-4 w-4" />
                                Edit
                              </Link>
                            </DropdownMenuItem>
                            {post.status === 'draft' && (
                              <>
                                <DropdownMenuItem onClick={() => handlePublish(post.id)} disabled={publishMutation.isPending}>
                                  <Send className="mr-2 h-4 w-4" />
                                  {publishMutation.isPending ? 'Publishing...' : 'Publish Now'}
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={() => openScheduleDialog(post)} disabled={scheduleMutation.isPending}>
                                  <Calendar className="mr-2 h-4 w-4" />
                                  Schedule
                                </DropdownMenuItem>
                              </>
                            )}
                            {post.status === 'scheduled' && (
                              <DropdownMenuItem onClick={() => handlePublish(post.id)} disabled={publishMutation.isPending}>
                                <Send className="mr-2 h-4 w-4" />
                                {publishMutation.isPending ? 'Publishing...' : 'Publish Now'}
                              </DropdownMenuItem>
                            )}
                            <DropdownMenuSeparator />
                            <DropdownMenuItem onClick={() => handleDelete(post.id)} className="text-destructive" disabled={deleteMutation.isPending}>
                              <Trash2 className="mr-2 h-4 w-4" />
                              {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between p-4 border-t">
                  <p className="text-sm text-muted-foreground">
                    Page {page} of {totalPages} • {data?.total} posts
                  </p>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage(p => Math.max(1, p - 1))}
                      disabled={page === 1}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                      disabled={page === totalPages}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Schedule Dialog */}
      <Dialog open={!!selectedPost} onOpenChange={(open) => !open && setSelectedPost(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Schedule Post</DialogTitle>
          </DialogHeader>
          <div className="py-4">
            <label className="block text-sm font-medium mb-2">Schedule for</label>
            <Input
              type="datetime-local"
              value={scheduleDate}
              onChange={(e) => setScheduleDate(e.target.value)}
              min={new Date().toISOString().slice(0, 16)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSelectedPost(null)}>Cancel</Button>
            <Button
              onClick={() => selectedPost && handleSchedule(selectedPost.id, scheduleDate)}
              disabled={!scheduleDate || scheduleMutation.isPending}
            >
              {scheduleMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : 'Schedule'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}