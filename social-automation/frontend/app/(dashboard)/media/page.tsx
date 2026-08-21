'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, Image as ImageIcon, Upload, Trash2, MoreVertical, Loader2, Download, Eye, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from '@/components/ui/DropdownMenu'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger } from '@/components/ui/Dialog'
import { Skeleton } from '@/components/ui/Skeleton'
import { Textarea } from '@/components/ui/Textarea'
import { useMedia, useUploadMedia, useDeleteMedia, useGenerateImage } from '@/hooks/useQueries'
import type { MediaAsset } from '@/types'
import toast from 'react-hot-toast'

const typeOptions = [
  { value: '', label: 'All Types' },
  { value: 'image', label: 'Images' },
  { value: 'video', label: 'Videos' },
  { value: 'generated', label: 'AI Generated' },
]

export default function MediaPage() {
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [page, setPage] = useState(1)
  const [selectedMedia, setSelectedMedia] = useState<string | null>(null)
  const [generatePrompt, setGeneratePrompt] = useState('')
  const [generateOpen, setGenerateOpen] = useState(false)

  const { data, isLoading } = useMedia({ type: typeFilter, page, page_size: 20 })
  const uploadMutation = useUploadMedia()
  const deleteMutation = useDeleteMedia()
  const generateMutation = useGenerateImage()

  const media = data?.items || []
  const totalPages = data?.pages || 1

  const handleFileUpload = async (files: FileList) => {
    for (const file of Array.from(files)) {
      try {
        await uploadMutation.mutateAsync({ file, alt_text: '', tags: '' })
      } catch {
        toast.error(`Failed to upload ${file.name}`)
      }
    }
  }

  const handleDelete = async (id: string) => {
    if (confirm('Delete this media?')) {
      try {
        await deleteMutation.mutateAsync(id)
      } catch {
        toast.error('Failed to delete')
      }
    }
  }

  const handleGenerate = async () => {
    if (!generatePrompt.trim()) return
    try {
      await generateMutation.mutateAsync({ prompt: generatePrompt })
      setGeneratePrompt('')
      setGenerateOpen(false)
    } catch {
      toast.error('Failed to generate image')
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Media Library</h1>
          <p className="text-muted-foreground mt-1">Manage your images, videos, and AI-generated content</p>
        </div>
        <div className="flex gap-2">
          <Dialog open={generateOpen} onOpenChange={setGenerateOpen}>
            <DialogTrigger asChild>
              <Button variant="outline">
                <Sparkles className="mr-2 h-4 w-4" />
                AI Generate
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Generate Image with AI</DialogTitle>
              </DialogHeader>
              <div className="py-4">
                <Textarea
                  value={generatePrompt}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setGeneratePrompt(e.target.value)}
                  placeholder="A professional photo of a modern office workspace..."
                  rows={4}
                />
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setGenerateOpen(false)}>Cancel</Button>
                <Button onClick={handleGenerate} disabled={generateMutation.isPending || !generatePrompt.trim()}>
                  {generateMutation.isPending ? 'Generating...' : 'Generate'}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Button onClick={() => document.getElementById('file-upload')?.click()}>
            <Upload className="mr-2 h-4 w-4" />
            Upload
          </Button>
        </div>
      </div>

      {/* Hidden file input */}
      <input
        id="file-upload"
        type="file"
        accept="image/*,video/*"
        multiple
        className="hidden"
        onChange={(e) => e.target.files && handleFileUpload(e.target.files)}
      />

      {/* Filters */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-col sm:flex-row gap-4">
            <div className="relative flex-1 max-w-sm">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search media..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-10"
              />
            </div>
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="Type" />
              </SelectTrigger>
              <SelectContent>
                {typeOptions.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {/* Media Grid */}
      <Card>
        <CardContent className="pt-0">
          {isLoading ? (
            <div className="p-8 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
              {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
                <Skeleton key={i} className="aspect-square rounded-lg" />
              ))}
            </div>
          ) : media.length === 0 ? (
            <div className="py-16 text-center">
              <ImageIcon className="mx-auto h-12 w-12 text-muted-foreground/50" />
              <p className="mt-4 text-muted-foreground">No media found</p>
              <Button className="mt-4" onClick={() => document.getElementById('file-upload')?.click()}>
                <Upload className="mr-2 h-4 w-4" />
                Upload your first file
              </Button>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4 p-4">
                {media.map((item: MediaAsset) => (
                  <div
                    key={item.id}
                    className="relative group aspect-square rounded-lg overflow-hidden border bg-muted/50"
                  >
                    {item.storage_path ? (
                      <img
                        src={item.storage_path}
                        alt={item.filename || 'Media'}
                        className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-105"
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center text-muted-foreground">
                        <ImageIcon className="h-8 w-8" />
                      </div>
                    )}
                    <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                      <Button variant="ghost" size="icon" className="bg-white/90" asChild>
                        <a href={item.storage_path} target="_blank" rel="noopener noreferrer">
                          <Eye className="h-4 w-4" />
                        </a>
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="bg-white/90"
                        onClick={() => handleDelete(item.id)}
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                    {item.source === 'ai-generated' && (
                      <Badge className="absolute bottom-2 left-2" variant="outline">
                        <Sparkles className="mr-1 h-3 w-3" />
                        AI
                      </Badge>
                    )}
                  </div>
                ))}
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between p-4 border-t">
                  <p className="text-sm text-muted-foreground">
                    Page {page} of {totalPages} • {data?.total} items
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
    </div>
  )
}