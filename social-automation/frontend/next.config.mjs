/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  transpilePackages: ['@hello-pangea/dnd'],
  // Allow requests from the Cloudflare Tunnel domain in dev mode.
  // Without this, Next.js dev server rejects cross-origin requests
  // from social.cloudless.gr, preventing the app from loading in the browser.
  allowedDevOrigins: [
    'social.cloudless.gr',
    'https://social.cloudless.gr',
    'localhost',
    '127.0.0.1',
    'http://localhost:8082',
    'http://127.0.0.1:8082',
  ],
  images: {
    remotePatterns: [
      { protocol: 'http',  hostname: 'localhost' },
      { protocol: 'https', hostname: 'avatars.githubusercontent.com' },
      { protocol: 'https', hostname: 'lh3.googleusercontent.com' },
      { protocol: 'https', hostname: 'platform-lookaside.fbsbx.com' },
      { protocol: 'https', hostname: 'pbs.twimg.com' },
      { protocol: 'https', hostname: 'media.licdn.com' },
    ],
  },
  // Security headers on every response — complements Cloudflare edge headers
  // (HSTS, nosniff) so the app is protected even if reached without the edge.
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          // microphone/camera allowed for same-origin only — the VoiceRecorder
          // (getUserMedia) and the embedded noVNC browser iframe need them.
          { key: 'Permissions-Policy', value: 'camera=(self), microphone=(self), geolocation=()' },
          // RFC 8288 Link headers for agent discovery
          { key: 'Link', value: [
            '</.well-known/api-catalog>; rel="api-catalog"; type="application/linkset+json"',
            '</.well-known/oauth-authorization-server>; rel="oauth-authorization-server"; type="application/json"',
            '</.well-known/oauth-protected-resource>; rel="oauth-protected-resource"; type="application/json"',
            '</.well-known/agent.json>; rel="agent-card"; type="application/json"',
            '</.well-known/mcp/server-card.json>; rel="mcp-server-card"; type="application/json"',
            '</.well-known/ai-catalog.json>; rel="ai-catalog"; type="application/json"',
            '</.well-known/agent-skills/index.json>; rel="agent-skills"; type="application/json"',
            '</api/v1/openapi.json>; rel="service-desc"; type="application/vnd.oai.openapi+json"',
            '</api-docs>; rel="service-doc"; type="text/html"',
            '</api/v1/health>; rel="status"',
            '</privacy>; rel="privacy-policy"; type="text/html"',
            '</auth.md>; rel="agent-auth"; type="text/markdown"',
          ].join(', ') },
        ],
      },
    ]
  },
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${process.env.API_INTERNAL_URL || 'http://social-api:8000'}/api/v1/:path*`,
      },
      // Public email-funnel shortcuts: /s/<token> survey, /u/<token> unsubscribe.
      {
        source: '/s/:token',
        destination: `${process.env.API_INTERNAL_URL || 'http://social-api:8000'}/s/:token`,
      },
      {
        source: '/u/:token',
        destination: `${process.env.API_INTERNAL_URL || 'http://social-api:8000'}/u/:token`,
      },
      // noVNC HTTP proxy (HTML, JS, CSS, images). The WebSocket upgrade
      // for VNC traffic is handled by server.js (custom server) because
      // Next.js rewrites don't support WS upgrades (issue #23147).
      {
        source: '/novnc/:path*',
        destination: `http://browser-novnc:6080/:path*`,
      },
    ]
  },
}

export default nextConfig
