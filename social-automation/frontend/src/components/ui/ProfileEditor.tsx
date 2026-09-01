'use client'

import { useState, useEffect, useRef } from 'react'
import { Loader2, Save, Upload, Image as ImageIcon, RefreshCw, User } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { Label } from '@/components/ui/Label'
import { Badge } from '@/components/ui/Badge'
import toast from 'react-hot-toast'
import type { SocialAccount } from '@/types'
import {
  useProfile,
  useUpdateProfile,
  useUploadProfileProfilePicture,
  useUploadProfileCoverPhoto,
  useProfileLogin,
} from '@/hooks/useQueries'

interface ProfileEditorProps {
  account: SocialAccount
  onClose?: () => void
}

const PLATFORM_LABELS: Record<string, string> = {
  instagram: 'Instagram',
  facebook: 'Facebook',
  linkedin: 'LinkedIn',
  twitter: 'X / Twitter',
  tiktok: 'TikTok',
  threads: 'Threads',
}

export function ProfileEditor({ account, onClose }: ProfileEditorProps) {
  const accountId = account.id
  const platform = account.platform
  const isBusiness = account.is_business

  const { data: profileData, isLoading, refetch } = useProfile(accountId)
  const updateMutation = useUpdateProfile()
  const pictureMutation = useUploadProfileProfilePicture()
  const coverMutation = useUploadProfileCoverPhoto()
  const loginMutation = useProfileLogin()

  const profile = profileData ?? {}

  const [about, setAbout] = useState('')
  const [biography, setBiography] = useState('')
  const [headline, setHeadline] = useState('')
  const [fullName, setFullName] = useState('')
  const [website, setWebsite] = useState('')
  const [location, setLocation] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [quotes, setQuotes] = useState('')

  const [loginUsername, setLoginUsername] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [showLogin, setShowLogin] = useState(false)

  const profilePicRef = useRef<HTMLInputElement>(null)
  const coverRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (profile) {
      setAbout(profile.about || '')
      setBiography(profile.biography || '')
      setHeadline(profile.headline || '')
      setFullName(profile.full_name || '')
      setWebsite(profile.website || '')
      setLocation(profile.location || '')
      setPhone(profile.phone || '')
      setEmail(profile.email || '')
      setQuotes(profile.quotes || '')
    }
  }, [profile])

  const needsLogin = platform === 'instagram' || (platform === 'facebook' && !isBusiness) || platform === 'linkedin'
  const isReadOnly = platform === 'threads'

  const handleSave = async () => {
    const data: Record<string, string> = {}
    if (about !== (profile.about || '')) data.about = about
    if (biography !== (profile.biography || '')) data.biography = biography
    if (headline !== (profile.headline || '')) data.headline = headline
    if (fullName !== (profile.full_name || '')) data.full_name = fullName
    if (website !== (profile.website || '')) data.website = website
    if (location !== (profile.location || '')) data.location = location
    if (phone !== (profile.phone || '')) data.phone = phone
    if (email !== (profile.email || '')) data.email = email
    if (quotes !== (profile.quotes || '')) data.quotes = quotes

    if (Object.keys(data).length === 0) {
      toast('No changes to save')
      return
    }

    await updateMutation.mutateAsync({ id: accountId, data })
    refetch()
  }

  const handlePicture = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (file.size > 4 * 1024 * 1024) {
      toast.error('Image must be under 4MB')
      return
    }
    await pictureMutation.mutateAsync({ id: accountId, file })
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

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!loginUsername || !loginPassword) {
      toast.error('Enter username and password')
      return
    }
    await loginMutation.mutateAsync({
      id: accountId,
      data: { username: loginUsername, password: loginPassword },
    })
    setShowLogin(false)
    setLoginPassword('')
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

  return (
    <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-slate-800">
              <User className="h-5 w-5 text-white" />
            </div>
            <div>
              <CardTitle className="text-base">
                {PLATFORM_LABELS[platform] || platform} Profile
                {isBusiness ? ' (Page)' : ' (Personal)'}
              </CardTitle>
              <CardDescription className="text-xs">
                {account.username || account.display_name || account.account_id}
              </CardDescription>
            </div>
          </div>
          <div className="flex gap-1">
            <Button variant="ghost" size="icon" onClick={() => refetch()} title="Refresh">
              <RefreshCw className="h-4 w-4" />
            </Button>
            {onClose && (
              <Button variant="ghost" size="icon" onClick={onClose} title="Close">
                ×
              </Button>
            )}
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-5">
        {needsLogin && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm">Session</Label>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowLogin(!showLogin)}
              >
                {showLogin ? 'Cancel' : 'Log in'}
              </Button>
            </div>
            {showLogin && (
              <form onSubmit={handleLogin} className="space-y-2 p-3 border rounded-md bg-muted/30">
                <Input
                  placeholder="Username / email"
                  value={loginUsername}
                  onChange={(e) => setLoginUsername(e.target.value)}
                />
                <Input
                  type="password"
                  placeholder="Password"
                  value={loginPassword}
                  onChange={(e) => setLoginPassword(e.target.value)}
                />
                <Button
                  type="submit"
                  size="sm"
                  disabled={loginMutation.isPending}
                >
                  {loginMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Log in'}
                </Button>
              </form>
            )}
          </div>
        )}

        {isReadOnly ? (
          <div className="text-sm text-muted-foreground">
            Threads does not support profile updates through the API.
          </div>
        ) : (
          <>
            <div className="relative rounded-lg overflow-hidden border">
              <div className="h-32 bg-muted relative">
                {profile.cover_url ? (
                  <img src={profile.cover_url} alt="Cover" className="w-full h-full object-cover" />
                ) : (
                  <div className="flex items-center justify-center h-full text-muted-foreground text-xs">
                    No cover photo
                  </div>
                )}
                {platform !== 'tiktok' && platform !== 'instagram' && (
                  <button
                    onClick={() => coverRef.current?.click()}
                    disabled={coverMutation.isPending}
                    className="absolute bottom-2 right-2 bg-black/60 hover:bg-black/80 text-white rounded px-2 py-1 text-xs flex items-center gap-1 transition-colors"
                  >
                    {coverMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
                    Upload Cover
                  </button>
                )}
                <input ref={coverRef} type="file" accept="image/png,image/jpeg" className="hidden" onChange={handleCover} />
              </div>
              <div className="px-4 pb-4 flex items-end gap-4 -mt-8">
                <div className="relative">
                  <div className="w-16 h-16 rounded-full border-4 border-background bg-muted overflow-hidden">
                    {profile.profile_pic_url ? (
                      <img src={profile.profile_pic_url} alt="Profile" className="w-full h-full object-cover" />
                    ) : (
                      <div className="flex items-center justify-center h-full">
                        <ImageIcon className="h-6 w-6 text-muted-foreground" />
                      </div>
                    )}
                  </div>
                  <button
                    onClick={() => profilePicRef.current?.click()}
                    disabled={pictureMutation.isPending}
                    className="absolute -bottom-1 -right-1 bg-primary text-primary-foreground rounded-full p-1 hover:bg-primary/90"
                  >
                    {pictureMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
                  </button>
                  <input ref={profilePicRef} type="file" accept="image/png,image/jpeg" className="hidden" onChange={handlePicture} />
                </div>
                <div className="pb-1">
                  <Badge variant="secondary" className="text-xs capitalize">
                    {platform}
                  </Badge>
                </div>
              </div>
            </div>

            <div className="space-y-3">
              {(platform === 'linkedin' || platform === 'twitter' || platform === 'tiktok') && (
                <div className="space-y-1">
                  <Label className="text-sm">Full Name / Nickname</Label>
                  <Input value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Display name" />
                </div>
              )}

              {platform === 'linkedin' && (
                <div className="space-y-1">
                  <Label className="text-sm">Headline</Label>
                  <Input value={headline} onChange={(e) => setHeadline(e.target.value)} placeholder="Professional headline" />
                </div>
              )}

              {(platform === 'facebook' || platform === 'instagram' || platform === 'tiktok') && (
                <div className="space-y-1">
                  <Label className="text-sm">{platform === 'instagram' || platform === 'tiktok' ? 'Biography' : 'About'}</Label>
                  <Textarea
                    value={platform === 'instagram' || platform === 'tiktok' ? biography : about}
                    onChange={(e) => platform === 'instagram' || platform === 'tiktok' ? setBiography(e.target.value) : setAbout(e.target.value)}
                    placeholder={platform === 'instagram' || platform === 'tiktok' ? 'Bio text' : 'About text'}
                    rows={3}
                  />
                </div>
              )}

              {platform === 'facebook' && !isBusiness && (
                <div className="space-y-1">
                  <Label className="text-sm">Quotes</Label>
                  <Input value={quotes} onChange={(e) => setQuotes(e.target.value)} placeholder="Favorite quotes" />
                </div>
              )}

              <div className="space-y-1">
                <Label className="text-sm">Website</Label>
                <Input value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="https://..." />
              </div>

              {(platform === 'twitter' || platform === 'facebook') && (
                <div className="space-y-1">
                  <Label className="text-sm">Location</Label>
                  <Input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="City, Country" />
                </div>
              )}

              {(platform === 'facebook' || platform === 'instagram') && (
                <div className="space-y-1">
                  <Label className="text-sm">Phone</Label>
                  <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="Phone number" />
                </div>
              )}

              {platform === 'instagram' && (
                <div className="space-y-1">
                  <Label className="text-sm">Email</Label>
                  <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" />
                </div>
              )}
            </div>

            <Button
              onClick={handleSave}
              disabled={updateMutation.isPending}
              className="w-full"
            >
              {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save Profile
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  )
}
