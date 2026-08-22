/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  transpilePackages: ['@hello-pangea/dnd'],
  images: {
    domains: ['localhost', 'avatars.githubusercontent.com', 'lh3.googleusercontent.com', 'platform-lookaside.fbsbx.com', 'pbs.twimg.com', 'media.licdn.com'],
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.API_INTERNAL_URL || 'http://social-api:8000'}/api/:path*`,
      },
    ]
  },
}

export default nextConfig