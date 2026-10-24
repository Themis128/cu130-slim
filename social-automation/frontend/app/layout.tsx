import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { Providers } from './providers'
import { WebMcpTools } from "@/components/webmcp-tools"

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
              <script dangerouslySetInnerHTML={{ __html: `(function(){
  function reg(){ var mc=document.modelContext; if(!mc||!mc.registerTool) return false;
    mc.registerTool({name:"list_platforms",description:"List the social platforms SocialAuto can publish to",inputSchema:{type:"object",properties:{}},execute:async function(){return {platforms:["linkedin","x","instagram","facebook","threads","tiktok","telegram","whatsapp"]};}});
    mc.registerTool({name:"check_api_health",description:"Check whether the SocialAuto API is healthy",inputSchema:{type:"object",properties:{}},execute:async function(){var r=await fetch("/api/v1/health");return {status:r.status,ok:r.ok};}});
    return true; }
  if(!reg()){ var n=0, iv=setInterval(function(){ if(reg()||++n>20) clearInterval(iv); },250); }
})();` }} />
      </head>
      <body className={`${inter.variable} font-sans antialiased`} suppressHydrationWarning>
        <Providers>{children}</Providers>
              <WebMcpTools />
      </body>
    </html>
  )
}