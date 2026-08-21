import { ReactNode } from 'react'
import { Layout } from '@/components/layout'

export const dynamic = 'force-dynamic'

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return <Layout>{children}</Layout>
}