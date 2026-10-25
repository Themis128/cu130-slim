'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/hooks/useAuth'
import { Loader2 } from 'lucide-react'
import { PublicNav } from '@/components/marketing/PublicNav'
import { PublicFooter } from '@/components/marketing/PublicFooter'
import { Hero } from '@/components/marketing/Hero'
import { FunnelSection } from '@/components/marketing/FunnelSection'
import { FeatureGrid } from '@/components/marketing/FeatureGrid'
import { PlatformSupport } from '@/components/marketing/PlatformSupport'
import { CTASection } from '@/components/marketing/CTASection'

export default function HomePage() {
  const router = useRouter()
  const { user, isLoading } = useAuth()

  useEffect(() => {
    if (!isLoading && user) {
      router.push('/dashboard')
    }
  }, [user, isLoading, router])

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  // Authenticated users are redirected above; show loader while that happens.
  if (user) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <PublicNav />
      <main className="flex-1">
        <Hero />
        <FunnelSection />
        <FeatureGrid />
        <PlatformSupport />
        <CTASection />
      {/* WebMCP declarative tools — agent-facing actions per the WebMCP draft */}
      <form {...{ toolname: "check_api_health", tooldescription: "Check whether the SocialAuto API is healthy" }} action="/api/v1/health" method="get" hidden>
        <input name="ping" value="1" {...{ toolparamdescription: "Ping flag; response body reports service health" }} />
      </form>
      <form {...{ toolname: "list_platforms", tooldescription: "List the social platforms SocialAuto can publish to" }} action="/api/v1/social/accounts" method="get" hidden>
        <input name="format" value="json" {...{ toolparamdescription: "Response format" }} />
      </form>
      </main>
      <PublicFooter />
    </div>
  )
}
