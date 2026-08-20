'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Mail, Lock, User, Loader2, AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/Card'
import { Separator } from '@/components/ui/Separator'
import { useAuth } from '@/hooks/useAuth'
import toast from 'react-hot-toast'

export function RegisterPage() {
  const router = useRouter()
  const { register } = useAuth()
  const [formData, setFormData] = useState({
    full_name: '',
    email: '',
    password: '',
    confirmPassword: '',
  })
  const [isLoading, setIsLoading] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const validate = () => {
    const newErrors: Record<string, string> = {}
    if (!formData.full_name.trim()) newErrors.full_name = 'Full name is required'
    if (!formData.email) newErrors.email = 'Email is required'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) newErrors.email = 'Invalid email address'
    if (!formData.password) newErrors.password = 'Password is required'
    else if (formData.password.length < 8) newErrors.password = 'Password must be at least 8 characters'
    if (formData.password !== formData.confirmPassword) newErrors.confirmPassword = 'Passwords do not match'
    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!validate()) return

    setIsLoading(true)
    try {
      await register(formData.full_name, formData.email, formData.password)
      toast.success('Account created successfully!')
      router.push('/dashboard')
      router.refresh()
    } catch (error) {
      const axiosError = error as { response?: { data?: { detail?: string } } }
      toast.error(axiosError.response?.data?.detail || 'Registration failed')
    } finally {
      setIsLoading(false)
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData(prev => ({ ...prev, [e.target.name]: e.target.value }))
    if (errors[e.target.name]) {
      setErrors(prev => ({ ...prev, [e.target.name]: '' }))
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 px-4 py-12">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl">Create your account</CardTitle>
          <CardDescription>Start automating your social media today</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="full_name">Full Name</Label>
              <Input
                id="full_name"
                name="full_name"
                type="text"
                placeholder="John Doe"
                value={formData.full_name}
                onChange={handleChange}
                error={errors.full_name}
                disabled={isLoading}
                autoComplete="name"
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                placeholder="you@example.com"
                value={formData.email}
                onChange={handleChange}
                error={errors.email}
                disabled={isLoading}
                autoComplete="email"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                placeholder="••••••••"
                value={formData.password}
                onChange={handleChange}
                error={errors.password}
                disabled={isLoading}
                autoComplete="new-password"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirmPassword">Confirm Password</Label>
              <Input
                id="confirmPassword"
                name="confirmPassword"
                type="password"
                placeholder="••••••••"
                value={formData.confirmPassword}
                onChange={handleChange}
                error={errors.confirmPassword}
                disabled={isLoading}
                autoComplete="new-password"
              />
            </div>
            <Button type="submit" className="w-full" isLoading={isLoading}>
              Create account
            </Button>
          </form>
        </CardContent>
        <CardFooter className="flex flex-col space-y-4">
          <Separator />
          <p className="text-center text-sm text-muted-foreground">
            Already have an account?{' '}
            <Link href="/auth/login" className="text-primary font-medium hover:underline">
              Sign in
            </Link>
          </p>
        </CardFooter>
      </Card>
    </div>
  )
}