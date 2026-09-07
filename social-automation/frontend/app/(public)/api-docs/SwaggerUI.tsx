'use client'

import { useEffect, useRef } from 'react'

// Load Swagger UI from CDN and render into a container div.
// This avoids adding swagger-ui-react as an npm dependency.
export default function SwaggerUI() {
  const containerRef = useRef<HTMLDivElement>(null)
  const loadedRef = useRef(false)

  useEffect(() => {
    if (loadedRef.current) return
    loadedRef.current = true

    const script = document.createElement('script')
    script.src = 'https://unpkg.com/swagger-ui-dist@5.18.2/swagger-ui-bundle.js'
    script.async = true
    script.onload = () => {
      if (containerRef.current && (window as any).SwaggerUIBundle) {
        (window as any).SwaggerUIBundle({
          url: typeof window !== 'undefined' && window.location.hostname === 'localhost'
            ? 'http://localhost:8083/openapi.json'
            : 'https://social.cloudless.gr/openapi.json',
          domNode: containerRef.current,
          deepLinking: true,
          presets: [(window as any).SwaggerUIBundle.presets.apis],
          layout: 'BaseLayout',
          requestInterceptor: (req: any) => {
            // Attach JWT if available in localStorage
            const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null
            if (token) {
              req.headers.Authorization = `Bearer ${token}`
            }
            return req
          },
        })
      }
    }

    // Load CSS
    const link = document.createElement('link')
    link.rel = 'stylesheet'
    link.href = 'https://unpkg.com/swagger-ui-dist@5.18.2/swagger-ui.css'

    document.head.appendChild(link)
    document.body.appendChild(script)

    return () => {
      // Cleanup is handled by loadedRef guard
    }
  }, [])

  return <div ref={containerRef} className="swagger-container" />
}
