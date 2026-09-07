'use client'

import { useState, useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/Card'
import { Separator } from '@/components/ui/Separator'
import { useAuth } from '@/hooks/useAuth'
import { teamsApi, authApi, setTokens } from '@/services/api'
import toast from 'react-hot-toast'

export default function AcceptInvitePage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { isAuthenticated, refreshUser } = useAuth()
  const token = searchParams.get('token')
  const [isLoading, setIsLoading] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteTeam, setInviteTeam] = useState('')

  // Decode the JWT payload (without verification — backend verifies).
  useEffect(() => {
    if (!token) return
    try {
      const payload = JSON.parse(atob(token.split('.')[1]))
      setInviteEmail(payload.invite_email || '')
      setInviteTeam(payload.invite_team_id || '')
    } catch {
      // Invalid token format — the backend will reject it.
    }
  }, [token])

  // If already logged in, try to accept the invite immediately.
  useEffect(() => {
    if (!token || !isAuthenticated) return
    let cancelled = false;
    (async () => {
      try {
        await teamsApi.acceptInvite(token)
        if (!cancelled) {
          toast.success('You have joined the team!')
          router.push('/dashboard')
          router.refresh()
        }
      } catch (error: unknown) {
        if (cancelled) return
        const axiosError = error as { response?: { data?: { detail?: string } } }
        toast.error(axiosError.response?.data?.detail || 'Failed to accept invite')
      }
    })()
    return () => { cancelled = true }
  }, [token, isAuthenticated, router])

  const [formData, setFormData] = useState({
    full_name: '',
    email: '',
    password: '',
    confirmPassword: '',
  })

  // Pre-fill email from invite token.
  useEffect(() => {
    if (inviteEmail) {
      setFormData(prev => ({ ...prev, email: inviteEmail }))
    }
  }, [inviteEmail])

  const validate = () => {
    if (!formData.full_name.trim()) { toast.error('Full name is required'); return false }
    if (!formData.email) { toast.error('Email is required'); return false }
    if (formData.password.length < 8) { toast.error('Password must be at least 8 characters'); return false }
    if (formData.password !== formData.confirmPassword) { toast.error('Passwords do not match'); return false }
    return true
  }

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!validate() || !token) return
    setIsLoading(true)
    try {
      // Register
      await authApi.register({ name: formData.full_name, email: formData.email, password: formData.password })
      // Auto-login
      const loginRes = await authApi.login({ email: formData.email, password: formData.password })
      setTokens(loginRes.data.access_token, loginRes.data.refresh_token)
      // Accept the invite
      await teamsApi.acceptInvite(token)
      await refreshUser()
      toast.success('Account created! You have joined the team.')
      router.push('/dashboard')
      router.refresh()
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { detail?: string } } }
      toast.error(axiosError.response?.data?.detail || 'Registration failed')
    } finally {
      setIsLoading(false)
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData(prev => ({ ...prev, [e.target.name]: e.target.value }))
  }

  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-muted/30 px-4 py-12">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <CardTitle className="text-2xl">Invalid Invite Link</CardTitle>
            <CardDescription>This invite link is missing a token. Please ask your team admin to resend the invitation.</CardDescription>
          </CardHeader>
          <CardFooter className="justify-center">
            <Link href="/login" className="text-primary font-medium hover:underline">Go to login</Link>
          </CardFooter>
        </Card>
      </div>
    )
  }

  if (isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-muted/30 px-4 py-12">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <CardTitle className="text-2xl">Accepting Invitation...</CardTitle>
            <CardDescription>You are being added to the team.</CardDescription>
          </CardHeader>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 px-4 py-12">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl">Join the team</CardTitle>
          <CardDescription>
            {inviteEmail
              ? `Create your account to join as ${inviteEmail}`
              : 'Create your account to get started'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleRegister} className="space-y-4" noValidate>
            <div className="space-y-2">
              <Label htmlFor="full_name">Full Name</Label>
              <Input id="full_name" name="full_name" type="text" placeholder="John Doe"
                value={formData.full_name} onChange={handleChange} disabled={isLoading}
                autoComplete="name" autoFocus />
            </div>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" name="email" type="email" placeholder="you@example.com"
                value={formData.email} onChange={handleChange} disabled={isLoading || !!inviteEmail}
                autoComplete="email" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input id="password" name="password" type="password" placeholder="••••••••"
                value={formData.password} onChange={handleChange} disabled={isLoading}
                autoComplete="new-password" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirmPassword">Confirm Password</Label>
              <Input id="confirmPassword" name="confirmPassword" type="password" placeholder="••••••••"
                value={formData.confirmPassword} onChange={handleChange} disabled={isLoading}
                autoComplete="new-password" />
            </div>
            <Button type="submit" className="w-full" isLoading={isLoading}>
              Create account & join
            </Button>
          </form>
        </CardContent>
        <CardFooter className="flex flex-col space-y-4">
          <Separator />
          <p className="text-center text-sm text-muted-foreground">
            Already have an account?{' '}
            <Link href="/login" className="text-primary font-medium hover:underline">
              Sign in
            </Link>
          </p>
        </CardFooter>
      </Card>
    </div>
  )
}
