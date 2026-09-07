'use client'

import dynamic from 'next/dynamic'

// Swagger UI loads from CDN — no npm dependency needed.
const SwaggerUI = dynamic(() => import('./SwaggerUI'), { ssr: false })

export default function ApiDocsPage() {
  return (
    <div className="min-h-screen bg-background">
      <div className="container mx-auto max-w-6xl px-4 py-8">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-foreground">API Documentation</h1>
          <p className="mt-2 text-muted-foreground">
            Interactive API reference for the SocialAuto platform. Use the &quot;Authorize&quot; button
            to authenticate with your JWT token.
          </p>
        </div>
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <SwaggerUI />
        </div>
      </div>
    </div>
  )
}
