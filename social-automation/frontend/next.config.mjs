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
      // Proxy noVNC through the frontend so it's served over HTTPS
      // (eliminates Mixed Content warnings when embedded in an iframe
      // on https://social.cloudless.gr). The WebSocket upgrade for VNC
      // traffic is handled by Cloudflare Tunnel which supports ws/wss.
      {
        source: '/novnc/:path*',
        destination: `http://browser-novnc:6080/:path*`,
      },
    ]
  },
}

export default nextConfig