import { ReactNode } from 'react'
import { PublicNav } from '@/components/marketing/PublicNav'
import { PublicFooter } from '@/components/marketing/PublicFooter'

export const metadata = {
  title: 'SocialAuto - AI Social Media Automation',
  description:
    'Agency-grade social media automation for founders and small teams — AI content generation, publishing across six networks plus Messenger and Telegram, smart scheduling, and analytics.',
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
