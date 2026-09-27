import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { Providers } from './providers'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })

export const metadata: Metadata = {
  title: 'SocialAuto - Social Media Automation Platform',
  description: 'Automate your social media with AI-powered workflows, scheduling, and analytics',
  other: {
    'tiktok-developers-site-verification': '30QWkDq9g0olcwcIDueeqBix84M0VCXn',
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Set .dark before hydration — stored theme wins, Cloudless dark is
            the default for new visitors, 'system' resolves via media query. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem('theme')||'dark';if(t==='system'){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.classList.toggle('dark',t==='dark')}catch(e){}`,
          }}
        />
      </head>
      <body className={`${inter.variable} font-sans antialiased`} suppressHydrationWarning>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
