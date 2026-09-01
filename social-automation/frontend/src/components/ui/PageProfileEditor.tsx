'use client'

import { useState, useEffect, useRef } from 'react'
import {
  Facebook, Loader2, Save, Upload, Image as ImageIcon, ShieldCheck,
  AlertCircle, ExternalLink, RefreshCw, Settings2,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'
import { Badge } from '@/components/ui/Badge'
import { Separator } from '@/components/ui/Separator'
import {
  usePageProfile, useUpdatePageProfile, useUploadProfilePicture,
  useUploadCoverPhoto, useAssignManageTask,
} from '@/hooks/useQueries'
import toast from 'react-hot-toast'

interface PageProfileEditorProps {
  accountId: string
  accountName?: string
}

export function PageProfileEditor({ accountId, accountName }: PageProfileEditorProps) {
  const { data: profileData, isLoading, refetch } = usePageProfile(accountId)
  const updateMutation = useUpdatePageProfile()
  const profilePicMutation = useUploadProfilePicture()
  const coverMutation = useUploadCoverPhoto()
  const manageTaskMutation = useAssignManageTask()

  const profile = profileData?.profile
  const tasks = profileData?.tasks || []

  const [about, setAbout] = useState('')
  const [description, setDescription] = useState('')
  const [website, setWebsite] = useState('')
  const [phone, setPhone] = useState('')
  const [hasManage, setHasManage] = useState(false)
  const [businessId, setBusinessId] = useState('')

  const profilePicRef = useRef<HTMLInputElement>(null)
  const coverRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (profile) {
      setAbout(profile.about || '')
      setDescription(profile.description || '')
      setWebsite(profile.website || '')
      setPhone(profile.phone || '')
    }
  }, [profile])

  useEffect(() => {
    setHasManage(tasks.includes('MANAGE'))
  }, [tasks])

  const handleSave = async () => {
    await updateMutation.mutateAsync({
      id: accountId,
      data: { about, description, website, phone },
    })
    refetch()
  }

  const handleProfilePic = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (file.size > 4 * 1024 * 1024) {
      toast.error('Image must be under 4MB')
      return
    }
    await profilePicMutation.mutateAsync({ id: accountId, file })
    if (profilePicRef.current) profilePicRef.current.value = ''
    refetch()
  }

  const handleCover = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (file.size > 4 * 1024 * 1024) {
      toast.error('Image must be under 4MB')
      return
    }
    await coverMutation.mutateAsync({ id: accountId, file })
    if (coverRef.current) coverRef.current.value = ''
    refetch()
  }

  const handleAssignManage = async () => {
    if (!businessId.trim()) {
      toast.error('Enter your Meta Business ID first')
      return
    }
    await manageTaskMutation.mutateAsync({
      id: accountId,
      businessId: businessId.trim(),
    })
    refetch()
  }

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-8 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    )
  }

  const profilePicUrl = profile?.picture?.data?.url
  const coverUrl = profile?.cover?.source

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-blue-700">
              <Facebook className="h-5 w-5 text-white" />
            </div>
            <div>
              <CardTitle className="text-base">Facebook Page Profile</CardTitle>
              <CardDescription className="text-xs">
                Manage {accountName || profile?.name || 'your Page'} metadata, profile picture, and cover photo
              </CardDescription>
            </div>
          </div>
          <Button variant="ghost" size="icon" onClick={() => refetch()} title="Refresh">
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Cover & profile picture preview */}
        <div className="relative rounded-lg overflow-hidden border">
          <div className="h-32 bg-muted relative">
            {coverUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={coverUrl} alt="Cover" className="w-full h-full object-cover" />
            ) : (
              <div className="flex items-center justify-center h-full text-muted-foreground text-xs">
                No cover photo
              </div>
            )}
            <button
              onClick={() => coverRef.current?.click()}
              disabled={coverMutation.isPending}
              className="absolute bottom-2 right-2 bg-black/60 hover:bg-black/80 text-white rounded px-2 py-1 text-xs flex items-center gap-1 transition-colors"
            >
              {coverMutation.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Upload className="h-3 w-3" />
              )}
              Upload Cover
            </button>
            <input
              ref={coverRef}
              type="file"
              accept="image/png,image/jpeg"
              className="hidden"
              onChange={handleCover}
            />
          </div>
          <div className="px-4 pb-4 flex items-end gap-4 -mt-8">
            <div className="relative">
              <div className="w-16 h-16 rounded-full border-4 border-background bg-muted overflow-hidden">
                {profilePicUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={profilePicUrl} alt="Profile" className="w-full h-full object-cover" />
                ) : (
                  <div className="flex items-center justify-center h-full">
                    <ImageIcon className="h-6 w-6 text-muted-foreground" />
                  </div>
                )}
              </div>
              <button
                onClick={() => profilePicRef.current?.click()}
                disabled={profilePicMutation.isPending}
                className="absolute -bottom-1 -right-1 bg-primary text-primary-foreground rounded-full p-1.5 transition-colors hover:bg-primary/90"
                title="Upload profile picture"
              >
                {profilePicMutation.isPending ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Upload className="h-3 w-3" />
                )}
              </button>
              <input
                ref={profilePicRef}
                type="file"
                accept="image/png,image/jpeg"
                className="hidden"
                onChange={handleProfilePic}
              />
            </div>
            <div className="flex-1 pb-1">
              <p className="font-semibold text-sm">{profile?.name || accountName || 'Facebook Page'}</p>
              <p className="text-xs text-muted-foreground">{profile?.category}</p>
            </div>
            <div className="flex items-center gap-2 pb-1">
              {hasManage ? (
                <Badge variant="success" className="text-[10px] gap-1">
                  <ShieldCheck className="h-3 w-3" /> MANAGE
                </Badge>
              ) : (
                <Badge variant="outline" className="text-[10px] gap-1">
                  <AlertCircle className="h-3 w-3" /> No MANAGE
                </Badge>
              )}
              <a
                href={profile?.link || `https://www.facebook.com/profile.php?id=${profile?.id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-muted-foreground hover:text-foreground"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </div>
          </div>
        </div>

        {/* MANAGE task warning */}
        {!hasManage && (
          <div className="rounded-lg border border-amber-300/60 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-800/60 p-3 space-y-2">
            <div className="flex items-start gap-2 text-xs text-amber-800 dark:text-amber-200">
              <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold">MANAGE task required</p>
                <p className="mt-0.5">
                  To update <strong>About</strong> and <strong>Description</strong>, your Facebook user needs the
                  MANAGE task on this Page. Enter your Meta Business ID below and click assign.
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <Input
                placeholder="Meta Business ID (e.g. 1558125105019725)"
                value={businessId}
                onChange={(e) => setBusinessId(e.target.value)}
                className="text-xs h-8"
              />
              <Button
                size="sm"
                variant="outline"
                onClick={handleAssignManage}
                disabled={manageTaskMutation.isPending}
              >
                {manageTaskMutation.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ShieldCheck className="h-3.5 w-3.5" />
                )}
                Assign MANAGE
              </Button>
            </div>
          </div>
        )}

        <Separator />

        {/* Editable fields */}
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="fb-about" className="text-xs">
              About <span className="text-muted-foreground">(max 100 chars)</span>
            </Label>
            <Input
              id="fb-about"
              value={about}
              onChange={(e) => setAbout(e.target.value.slice(0, 100))}
              maxLength={100}
              placeholder="Short tagline for your Page"
              disabled={!hasManage}
            />
            <p className="text-[10px] text-muted-foreground text-right">{about.length}/100</p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="fb-description" className="text-xs">Description</Label>
            <Textarea
              id="fb-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Longer description of your business"
              rows={4}
              disabled={!hasManage}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="fb-website" className="text-xs">Website</Label>
              <Input
                id="fb-website"
                value={website}
                onChange={(e) => setWebsite(e.target.value)}
                placeholder="https://example.com"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="fb-phone" className="text-xs">Phone</Label>
              <Input
                id="fb-phone"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+30..."
              />
            </div>
          </div>
        </div>

        {/* Save button */}
        <div className="flex justify-end gap-2">
          <Button
            onClick={handleSave}
            disabled={updateMutation.isPending || (!hasManage && !website && !phone)}
          >
            {updateMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Save className="mr-2 h-4 w-4" />
            )}
            Save Changes
          </Button>
        </div>

        {/* Stats footer */}
        {profile && (
          <div className="flex items-center gap-4 text-xs text-muted-foreground border-t pt-3">
            <span>Followers: {profile.fan_count ?? 0}</span>
            <span>·</span>
            <span>Category: {profile.category || 'N/A'}</span>
            {profile.link && (
              <>
                <span>·</span>
                <a href={profile.link} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline flex items-center gap-1">
                  View on Facebook <ExternalLink className="h-3 w-3" />
                </a>
              </>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
