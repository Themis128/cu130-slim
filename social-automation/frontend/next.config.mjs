/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  transpilePackages: ['@hello-pangea/dnd'],
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
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${process.env.API_INTERNAL_URL || 'http://social-api:8000'}/api/v1/:path*`,
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