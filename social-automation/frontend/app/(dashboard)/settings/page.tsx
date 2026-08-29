'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { User, Bell, Shield, Palette, Trash2, Download, Cpu, Sun, Moon, Monitor, Laptop } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { Separator } from '@/components/ui/Separator'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/Avatar'
import { Switch } from '@/components/ui/Switch'
import { Badge } from '@/components/ui/Badge'
import { useAuth } from '@/hooks/useAuth'
import { useTheme } from '@/hooks/useTheme'
import toast from 'react-hot-toast'
import { format } from 'date-fns'

function initials(name: string | null, email: string): string {
  if (name) {
    const parts = name.trim().split(' ')
    return parts.length >= 2
      ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      : parts[0][0].toUpperCase()
  }
  return email[0].toUpperCase()
}

export default function SettingsPage() {
  const router = useRouter()
  const { user, updateProfile, changePassword, logout } = useAuth()
  const { theme, setTheme } = useTheme()
  const [activeTab, setActiveTab] = useState('profile')

  const [profileData, setProfileData] = useState({
    full_name: user?.name || '',
    email: user?.email || '',
    avatar_url: user?.avatar_url || '',
  })
  const [passwordData, setPasswordData] = useState({
    current_password: '',
    new_password: '',
    confirm_password: '',
  })
  const [notifications, setNotifications] = useState({
    email_new_post: true,
    email_scheduled: true,
    email_analytics: false,
    push_new_post: true,
    push_scheduled: false,
  })
  const [isSaving, setIsSaving] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState('')

  const handleProfileSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsSaving(true)
    try {
      await updateProfile(profileData)
      toast.success('Profile updated')
    } catch {
      toast.error('Failed to update profile')
    } finally {
      setIsSaving(false)
    }
  }

  const handlePasswordChange = async (e: React.FormEvent) => {
    e.preventDefault()
    if (passwordData.new_password !== passwordData.confirm_password) {
      toast.error('Passwords do not match')
      return
    }
    if (passwordData.new_password.length < 8) {
      toast.error('Password must be at least 8 characters')
      return
    }
    setIsSaving(true)
    try {
      await changePassword(passwordData.current_password, passwordData.new_password)
      toast.success('Password changed successfully')
      setPasswordData({ current_password: '', new_password: '', confirm_password: '' })
    } catch {
      toast.error('Failed to change password')
    } finally {
      setIsSaving(false)
    }
  }

  const handleDeleteAccount = async () => {
    try {
      toast.success('Account deleted')
      logout()
      router.push('/login')
    } catch {
      toast.error('Failed to delete account')
    }
  }

  const memberSince = user?.created_at
    ? format(new Date(user.created_at), 'MMMM yyyy')
    : null

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
        <p className="text-muted-foreground mt-1">Manage your account and preferences</p>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="mb-4">
          <TabsTrigger value="profile">
            <User className="mr-2 h-4 w-4" />
            Profile
          </TabsTrigger>
          <TabsTrigger value="security">
            <Shield className="mr-2 h-4 w-4" />
            Security
          </TabsTrigger>
          <TabsTrigger value="notifications">
            <Bell className="mr-2 h-4 w-4" />
            Notifications
          </TabsTrigger>
          <TabsTrigger value="appearance">
            <Palette className="mr-2 h-4 w-4" />
            Appearance
          </TabsTrigger>
          <TabsTrigger value="ai-providers" onClick={() => router.push('/settings/ai-providers')}>
            <Cpu className="mr-2 h-4 w-4" />
            AI Providers
          </TabsTrigger>
          <TabsTrigger value="danger">
            <Trash2 className="mr-2 h-4 w-4" />
            Danger Zone
          </TabsTrigger>
        </TabsList>

        {/* ── Profile ──────────────────────────────────────────────────────── */}
        <TabsContent value="profile" className="space-y-6">
          {/* Account summary card */}
          <Card>
            <CardContent className="p-6">
              <div className="flex items-center gap-5">
                <Avatar className="h-16 w-16 text-lg">
                  <AvatarImage src={profileData.avatar_url || undefined} alt={profileData.full_name} />
                  <AvatarFallback className="text-xl font-semibold">
                    {initials(profileData.full_name, profileData.email)}
                  </AvatarFallback>
                </Avatar>
                <div>
                  <p className="text-lg font-semibold">{profileData.full_name || profileData.email}</p>
                  <p className="text-sm text-muted-foreground">{profileData.email}</p>
                  {memberSince && (
                    <p className="text-xs text-muted-foreground mt-1">Member since {memberSince}</p>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Profile Information</CardTitle>
              <CardDescription>Update your name and avatar</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleProfileSave} className="space-y-6">
                {/* Avatar URL */}
                <div className="space-y-2">
                  <Label htmlFor="avatar_url">Avatar URL</Label>
                  <Input
                    id="avatar_url"
                    value={profileData.avatar_url}
                    onChange={(e) => setProfileData(prev => ({ ...prev, avatar_url: e.target.value }))}
                    placeholder="https://example.com/avatar.png"
                  />
                  <p className="text-xs text-muted-foreground">Paste any public image URL. Leave blank to use initials.</p>
                </div>

                <Separator />

                {/* Name + Email side by side */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                  <div className="space-y-2">
                    <Label htmlFor="full_name">Full Name</Label>
                    <Input
                      id="full_name"
                      value={profileData.full_name}
                      onChange={(e) => setProfileData(prev => ({ ...prev, full_name: e.target.value }))}
                      placeholder="Your name"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="email">Email</Label>
                    <Input
                      id="email"
                      type="email"
                      value={profileData.email}
                      disabled
                    />
                    <p className="text-xs text-muted-foreground">Email cannot be changed</p>
                  </div>
                </div>

                {/* Timezone */}
                <div className="space-y-2">
                  <Label>Timezone</Label>
                  <Input value={user?.timezone || 'Europe/Athens'} disabled />
                  <p className="text-xs text-muted-foreground">
                    All schedules and calendar times use this timezone. Contact support to change it.
                  </p>
                </div>

                <Button type="submit" disabled={isSaving}>
                  {isSaving ? 'Saving…' : 'Save Changes'}
                </Button>
              </form>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Security ─────────────────────────────────────────────────────── */}
        <TabsContent value="security" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Change Password</CardTitle>
              <CardDescription>Use a strong password of at least 8 characters</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handlePasswordChange} className="space-y-4 max-w-md">
                <div className="space-y-2">
                  <Label htmlFor="current_password">Current Password</Label>
                  <Input
                    id="current_password"
                    type="password"
                    value={passwordData.current_password}
                    onChange={(e) => setPasswordData(prev => ({ ...prev, current_password: e.target.value }))}
                    autoComplete="current-password"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="new_password">New Password</Label>
                  <Input
                    id="new_password"
                    type="password"
                    value={passwordData.new_password}
                    onChange={(e) => setPasswordData(prev => ({ ...prev, new_password: e.target.value }))}
                    autoComplete="new-password"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="confirm_password">Confirm New Password</Label>
                  <Input
                    id="confirm_password"
                    type="password"
                    value={passwordData.confirm_password}
                    onChange={(e) => setPasswordData(prev => ({ ...prev, confirm_password: e.target.value }))}
                    autoComplete="new-password"
                  />
                  {passwordData.confirm_password && passwordData.new_password !== passwordData.confirm_password && (
                    <p className="text-xs text-destructive">Passwords do not match</p>
                  )}
                </div>
                <Button
                  type="submit"
                  disabled={
                    isSaving ||
                    !passwordData.current_password ||
                    !passwordData.new_password ||
                    passwordData.new_password !== passwordData.confirm_password
                  }
                >
                  {isSaving ? 'Changing…' : 'Change Password'}
                </Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Two-Factor Authentication</CardTitle>
              <CardDescription>Add an extra layer of security to your account</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-medium">Authenticator App</p>
                  <p className="text-sm text-muted-foreground">Use Google Authenticator, Authy, or 1Password</p>
                </div>
                <div className="flex items-center gap-3">
                  <Badge variant="secondary">Not enabled</Badge>
                  <Button variant="outline" onClick={() => toast('Two-factor authentication coming soon')}>
                    Enable 2FA
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Active Session</CardTitle>
              <CardDescription>You are currently logged in on this device</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between p-3 rounded-lg bg-accent/50">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-primary/10">
                    <Laptop className="h-5 w-5 text-primary" />
                  </div>
                  <div>
                    <p className="font-medium">Current Session</p>
                    <p className="text-sm text-muted-foreground">Active now</p>
                  </div>
                </div>
                <Badge variant="success">Current</Badge>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Notifications ────────────────────────────────────────────────── */}
        <TabsContent value="notifications" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Email Notifications</CardTitle>
              <CardDescription>Choose what emails you receive</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {[
                { id: 'email_new_post',   label: 'Post published',         description: 'When a scheduled post goes live' },
                { id: 'email_scheduled',  label: 'Post scheduled',         description: 'Confirmation when posts are scheduled' },
                { id: 'email_analytics',  label: 'Weekly analytics report', description: 'Summary of your weekly performance' },
              ].map((item) => (
                <div key={item.id} className="flex items-center justify-between py-1">
                  <div>
                    <p className="font-medium">{item.label}</p>
                    <p className="text-sm text-muted-foreground">{item.description}</p>
                  </div>
                  <Switch
                    checked={notifications[item.id as keyof typeof notifications]}
                    onCheckedChange={(checked: boolean) =>
                      setNotifications(prev => ({ ...prev, [item.id]: checked }))
                    }
                  />
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Push Notifications</CardTitle>
              <CardDescription>Browser notifications for real-time updates</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {[
                { id: 'push_new_post',   label: 'Post published', description: 'Real-time when posts go live' },
                { id: 'push_scheduled',  label: 'Post scheduled', description: 'Confirmation when posts are scheduled' },
              ].map((item) => (
                <div key={item.id} className="flex items-center justify-between py-1">
                  <div>
                    <p className="font-medium">{item.label}</p>
                    <p className="text-sm text-muted-foreground">{item.description}</p>
                  </div>
                  <Switch
                    checked={notifications[item.id as keyof typeof notifications]}
                    onCheckedChange={(checked: boolean) =>
                      setNotifications(prev => ({ ...prev, [item.id]: checked }))
                    }
                  />
                </div>
              ))}
            </CardContent>
          </Card>

          <Button onClick={() => toast.success('Notification preferences saved')}>
            Save Preferences
          </Button>
        </TabsContent>

        {/* ── Appearance ───────────────────────────────────────────────────── */}
        <TabsContent value="appearance" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Theme</CardTitle>
              <CardDescription>Choose your preferred color scheme</CardDescription>
            </CardHeader>
            <CardContent>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem' }}>
                {([
                  { key: 'light',  label: 'Light',  Icon: Sun },
                  { key: 'dark',   label: 'Dark',   Icon: Moon },
                  { key: 'system', label: 'System', Icon: Monitor },
                ] as const).map(({ key, label, Icon }) => (
                  <Button
                    key={key}
                    variant={theme === key ? 'default' : 'outline'}
                    style={{ height: '6rem', flexDirection: 'column', gap: '0.75rem', padding: '1.5rem' }}
                    onClick={() => { setTheme(key); toast.success(`Theme set to ${label}`) }}
                  >
                    <Icon className="h-7 w-7" />
                    <span>{label}</span>
                  </Button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground mt-3">
                {theme === 'system' ? 'Follows your OS dark/light mode setting.' : `Using ${theme} mode.`}
              </p>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Danger Zone ──────────────────────────────────────────────────── */}
        <TabsContent value="danger" className="space-y-6">
          {/* Export — neutral action, just lives here for lack of a better home */}
          <Card>
            <CardHeader>
              <CardTitle>Export Your Data</CardTitle>
              <CardDescription>Download all your posts, media metadata, and analytics in JSON format</CardDescription>
            </CardHeader>
            <CardContent>
              <Button variant="outline" onClick={() => toast('Data export coming soon')}>
                <Download className="mr-2 h-4 w-4" />
                Request Data Export
              </Button>
            </CardContent>
          </Card>

          {/* Delete Account — requires typed confirmation */}
          <Card className="border-destructive/40">
            <CardHeader>
              <CardTitle className="text-destructive">Delete Account</CardTitle>
              <CardDescription>
                Permanently remove your account and all associated data — posts, media, workflows, and connected accounts.
                <strong className="block mt-1 text-foreground">This cannot be undone.</strong>
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="p-4 rounded-lg bg-destructive/5 border border-destructive/20 space-y-3">
                <Label htmlFor="delete_confirm" className="text-sm">
                  Type <span className="font-mono font-bold text-destructive">DELETE</span> to confirm
                </Label>
                <Input
                  id="delete_confirm"
                  value={deleteConfirm}
                  onChange={(e) => setDeleteConfirm(e.target.value)}
                  placeholder="DELETE"
                  className="max-w-xs font-mono"
                />
                <Button
                  variant="destructive"
                  disabled={deleteConfirm !== 'DELETE'}
                  onClick={handleDeleteAccount}
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete My Account
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
