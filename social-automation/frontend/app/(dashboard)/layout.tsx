'use client'

import { ReactNode, useEffect } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { useAuth } from '@/hooks/useAuth'
import { Layout } from '@/components/layout'
import { TourProvider } from '@/hooks/useTour'
import { AdvisorProvider } from '@/hooks/useAdvisor'
import { TourOverlay } from '@/components/tour/TourOverlay'
import { AdvisorCard } from '@/components/advisor/AdvisorCard'
import { Loader2 } from 'lucide-react'

export const dynamic = 'force-dynamic'

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading, user } = useAuth()
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push('/login')
    }
  }, [isAuthenticated, isLoading, router])

  // Redirect to onboarding wizard if the user hasn't completed it yet
  // (unless they're already on the onboarding page)
  useEffect(() => {
    if (!isLoading && isAuthenticated && user && user.onboarding_completed === false && pathname !== '/onboarding') {
      router.push('/onboarding')
    }
  }, [isLoading, isAuthenticated, user, pathname, router])

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return null
  }

  return (
    <TourProvider>
      <AdvisorProvider>
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-[100] focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
        >
          Skip to main content
        </a>
        <Layout>{children}</Layout>
        <TourOverlay />
        <AdvisorCard />
      </AdvisorProvider>
    </TourProvider>
  )
}
