import { ReactNode } from 'react'
import { PublicNav } from '@/components/marketing/PublicNav'
import { PublicFooter } from '@/components/marketing/PublicFooter'

export const metadata = {
  title: 'SocialAuto - AI Social Media Automation',
  description:
    'Automate your social media with AI-powered content generation, multi-platform publishing, smart scheduling, and analytics.',
}

export default function PublicLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <PublicNav />
      <main className="flex-1">{children}</main>
      <PublicFooter />
    </div>
  )
}
