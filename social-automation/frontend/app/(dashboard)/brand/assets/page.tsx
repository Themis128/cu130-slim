'use client'

import { useState } from 'react'
import { ArrowLeft, Plus, Trash2, Package } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { useBrand, useBrandAssets, useCreateBrandAsset, useDeleteBrandAsset } from '@/hooks/useQueries'

const ASSET_TYPES = ['logo', 'favicon', 'og_image', 'template', 'color_swatch', 'font', 'other']

export default function BrandAssetsPage() {
  const { data: brand, isLoading: brandLoading } = useBrand()
  const { data: assets = [], isLoading: assetsLoading } = useBrandAssets()
  const createAsset = useCreateBrandAsset()
  const deleteAsset = useDeleteBrandAsset()
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [assetType, setAssetType] = useState('logo')
  const [fileUrl, setFileUrl] = useState('')

  if (brandLoading || assetsLoading) {
    return <div className="flex items-center justify-center min-h-[60vh]"><p className="text-muted-foreground">Loading...</p></div>
  }

  if (!brand) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground mb-4">Create a brand first</p>
        <Link href="/brand"><Button>Go to Brand</Button></Link>
      </div>
    )
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name) return
    createAsset.mutate({
      name,
      asset_type: assetType,
      file_url: fileUrl || undefined,
    }, {
      onSuccess: () => {
        setShowForm(false)
        setName('')
        setFileUrl('')
        setAssetType('logo')
      },
    })
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link href="/brand">
            <Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button>
          </Link>
          <h1 className="text-2xl font-bold">Brand Assets</h1>
        </div>
        <Button onClick={() => setShowForm(!showForm)}>
          <Plus className="mr-2 h-4 w-4" /> Add Asset
        </Button>
      </div>

      {showForm && (
        <Card>
          <CardHeader>
            <CardTitle>New Brand Asset</CardTitle>
            <CardDescription>Add a logo, template, OG image, or other brand asset</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label htmlFor="name">Name</Label>
                  <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Primary Logo" required />
                </div>
                <div>
                  <Label htmlFor="type">Asset Type</Label>
                  <select
                    id="type"
                    value={assetType}
                    onChange={(e) => setAssetType(e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    {ASSET_TYPES.map((t) => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <Label htmlFor="url">File URL (optional)</Label>
                <Input id="url" value={fileUrl} onChange={(e) => setFileUrl(e.target.value)} placeholder="https://..." />
              </div>
              <div className="flex gap-2">
                <Button type="submit" disabled={createAsset.isPending}>Save Asset</Button>
                <Button type="button" variant="outline" onClick={() => setShowForm(false)}>Cancel</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {assets.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Package className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
            <p className="text-muted-foreground mb-2">No brand assets yet</p>
            <p className="text-sm text-muted-foreground">Add logos, templates, OG images, and other brand files here.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {assets.map((asset: any) => (
            <Card key={asset.id}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{asset.name}</span>
                      <Badge variant="secondary">{asset.asset_type?.replace('_', ' ')}</Badge>
                    </div>
                    {asset.file_url && (
                      <a href={asset.file_url} target="_blank" rel="noopener noreferrer" className="text-sm text-blue-500 hover:underline">
                        View file
                      </a>
                    )}
                    {asset.media_asset_id && (
                      <p className="text-xs text-muted-foreground">Linked to media library</p>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => deleteAsset.mutate(asset.id)}
                    aria-label="Delete asset"
                  >
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
