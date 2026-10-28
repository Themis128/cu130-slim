'use client'

import { useRouter } from 'next/navigation'
import { ArrowLeft } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { BrandKitWizard } from '@/components/ui/BrandKitWizard'

export default function BrandOnboardingPage() {
  const router = useRouter()

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/brand">
          <Button variant="ghost" size="icon"><ArrowLeft className="h-5 w-5" /></Button>
        </Link>
        <div>
          <h1 className="text-2xl font-bold">Brand Kit Wizard</h1>
          <p className="text-sm text-muted-foreground">Set up your brand in 3 quick steps</p>
        </div>
      </div>

      <BrandKitWizard onComplete={() => router.push('/brand')} />
    </div>
  )
}
