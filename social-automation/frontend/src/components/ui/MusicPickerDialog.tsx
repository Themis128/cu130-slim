'use client'

import { useState } from 'react'
import { Music, Search, Check, FileAudio } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/Dialog'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Skeleton } from '@/components/ui/Skeleton'
import { useMedia } from '@/hooks/useQueries'
import type { MediaAsset } from '@/types'

interface MusicPickerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called with the selected audio asset. */
  onSelect: (asset: MediaAsset) => void
  title?: string
}

const AUDIO_MIME_PREFIXES = ['audio/']
const AUDIO_EXTENSIONS = ['.mp3', '.wav', '.aac', '.m4a', '.ogg', '.flac']

function isAudioAsset(asset: MediaAsset): boolean {
  const mime = (asset.mime_type || '').toLowerCase()
  if (AUDIO_MIME_PREFIXES.some((p) => mime.startsWith(p))) return true
  const name = (asset.filename || asset.storage_path || '').toLowerCase()
  return AUDIO_EXTENSIONS.some((ext) => name.endsWith(ext))
}

export function MusicPickerDialog({
  open,
  onOpenChange,
  onSelect,
  title = 'Select Music Track',
}: MusicPickerDialogProps) {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { data, isLoading } = useMedia({ page, page_size: 24 })
  const allAssets: MediaAsset[] = data?.assets ?? data?.items ?? (Array.isArray(data) ? data : [])
  const audioAssets = allAssets.filter(isAudioAsset)
  const totalPages = Math.max(1, Math.ceil((data?.total ?? allAssets.length) / 24))

  const filtered = search
    ? audioAssets.filter((a) =>
        (a.filename ?? '').toLowerCase().includes(search.toLowerCase()) ||
        (a.alt_text ?? '').toLowerCase().includes(search.toLowerCase()) ||
        (a.tags ?? []).some((t) => t.toLowerCase().includes(search.toLowerCase()))
      )
    : audioAssets

  function confirm() {
    const pick = audioAssets.find((a) => a.id === selectedId)
    if (pick) {
      onSelect(pick)
    }
    setSelectedId(null)
    onOpenChange(false)
  }

  function handleClose() {
    setSelectedId(null)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col gap-0 p-0">
        <DialogHeader className="px-6 pt-6 pb-4 border-b">
          <DialogTitle className="flex items-center gap-2">
            <Music className="h-5 w-5" />
            {title}
          </DialogTitle>
          <div className="relative mt-3">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search by filename, alt text, or tags…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              className="pl-9"
            />
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto p-4">
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-14 rounded-lg" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-3">
              <FileAudio className="h-10 w-10 opacity-30" />
              <p className="text-sm">
                {search
                  ? `No audio results for "${search}"`
                  : 'No audio files in your media library yet. Upload MP3, WAV, AAC, M4A, OGG, or FLAC files.'}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {filtered.map((asset) => {
                const isSelected = selectedId === asset.id
                return (
                  <button
                    key={asset.id}
                    type="button"
                    onClick={() => setSelectedId(asset.id)}
                    className={`flex items-center gap-3 w-full rounded-lg border-2 p-3 transition-all text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                      isSelected
                        ? 'border-primary ring-2 ring-primary/30 bg-primary/5'
                        : 'border-transparent hover:border-muted-foreground/30 hover:bg-muted/30'
                    }`}
                  >
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                      <Music className="h-5 w-5 text-primary" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium truncate">
                        {asset.filename || asset.storage_path || 'Audio track'}
                      </p>
                      <p className="text-xs text-muted-foreground truncate">
                        {asset.mime_type || 'audio'}
                        {asset.duration_seconds ? ` · ${asset.duration_seconds}s` : ''}
                        {asset.size_bytes ? ` · ${Math.round(asset.size_bytes / 1024)}KB` : ''}
                      </p>
                    </div>
                    {isSelected && (
                      <div className="rounded-full bg-primary p-1 shrink-0">
                        <Check className="h-3 w-3 text-primary-foreground" />
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
          <Button onClick={confirm} disabled={!selectedId}>
            Select Track
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
