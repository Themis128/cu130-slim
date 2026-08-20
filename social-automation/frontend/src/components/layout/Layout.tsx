'use client'

import { ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { cn } from '@/lib/utils'

interface LayoutProps {
  children: ReactNode
}

export function Layout({ children }: LayoutProps) {
  return (
    <div className="flex h-screen bg-background font-sans antialiased">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6 md:p-8 lg:p-10">
          <div className="mx-auto max-w-7xl">{children}</div>
        </main>
      </div>
    </div>
  )
}