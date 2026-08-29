'use client'

import { useState } from 'react'
import { Image as ImageIcon, Search, Check, Upload, Sparkles, Loader2 } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/Dialog'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Skeleton } from '@/components/ui/Skeleton'
import { useMedia } from '@/hooks/useQueries'
import { mediaUrl } from '@/services/api'
import type { MediaAsset } from '@/types'

interface MediaPickerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called with the selected asset(s). */
  onSelect: (assets: MediaAsset[]) => void
  multiple?: boolean
  /** Optional label shown in the header. */
  title?: string
}

export function MediaPickerDialog({
  open,
  onOpenChange,
  onSelect,
  multiple = false,
  title = 'Select from Media Library',
}: MediaPickerDialogProps) {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const { data, isLoading } = useMedia({ page, page_size: 24 })
  const allAssets: MediaAsset[] = data?.assets ?? data?.items ?? (Array.isArray(data) ? data : [])
  const totalPages = Math.max(1, Math.ceil((data?.total ?? allAssets.length) / 24))

  const filtered = search
    ? allAssets.filter((a) =>
        (a.filename ?? '').toLowerCase().includes(search.toLowerCase()) ||
        (a.alt_text ?? '').toLowerCase().includes(search.toLowerCase())
      )
    : allAssets

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        if (!multiple) next.clear()
        next.add(id)
      }
      return next
    })
  }

  function confirm() {
    const picks = allAssets.filter((a) => selected.has(a.id))
    onSelect(picks)
    setSelected(new Set())
    onOpenChange(false)
  }

  function handleClose() {
    setSelected(new Set())
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col gap-0 p-0">
        <DialogHeader className="px-6 pt-6 pb-4 border-b">
          <DialogTitle>{title}</DialogTitle>
          <div className="relative mt-3">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search by filename or alt text…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              className="pl-9"
            />
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto p-4">
          {isLoading ? (
            <div className="grid grid-cols-4 sm:grid-cols-6 gap-3">
              {Array.from({ length: 12 }).map((_, i) => (
                <Skeleton key={i} className="aspect-square rounded-lg" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-3">
              <ImageIcon className="h-10 w-10 opacity-30" />
              <p className="text-sm">{search ? `No results for "${search}"` : 'No media yet'}</p>
            </div>
          ) : (
            <div className="grid grid-cols-4 sm:grid-cols-6 gap-3">
              {filtered.map((asset) => {
                const isSelected = selected.has(asset.id)
                return (
                  <button
                    key={asset.id}
                    type="button"
                    onClick={() => toggle(asset.id)}
                    className={`relative aspect-square rounded-lg overflow-hidden border-2 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                      isSelected
                        ? 'border-primary ring-2 ring-primary/30'
                        : 'border-transparent hover:border-muted-foreground/30'
                    }`}
                  >
                    {asset.storage_path ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={mediaUrl(asset.storage_path)}
                        alt={asset.alt_text || asset.filename || ''}
                        loading="lazy"
                        className="h-full w-full object-cover"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                      />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center bg-muted">
                        <ImageIcon className="h-5 w-5 text-muted-foreground" />
                      </div>
                    )}
                    {isSelected && (
                      <div className="absolute inset-0 bg-primary/20 flex items-center justify-center">
                        <div className="rounded-full bg-primary p-1">
                          <Check className="h-3 w-3 text-primary-foreground" />
                        </div>
                      </div>
                    )}
                    {(asset.source === 'ai-generated' || asset.source?.startsWith('n8n')) && (
                      <div className="absolute top-1 right-1">
                        <Sparkles className="h-3 w-3 text-white drop-shadow" />
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-between px-6 py-2 border-t text-sm text-muted-foreground">
            <span>Page {page} of {totalPages}</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>
                Previous
              </Button>
              <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages}>
                Next
              </Button>
            </div>
          </div>
        )}

        <DialogFooter className="px-6 py-4 border-t">
          <Button variant="outline" onClick={handleClose}>Cancel</Button>
          <Button onClick={confirm} disabled={selected.size === 0}>
            {multiple
              ? `Select ${selected.size > 0 ? selected.size : ''} image${selected.size !== 1 ? 's' : ''}`
              : 'Select'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
