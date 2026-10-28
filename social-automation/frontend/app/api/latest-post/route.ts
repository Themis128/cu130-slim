import { NextResponse } from 'next/server'

// Server-side proxy for the cloudless.gr blog index — the blog API sends no
// CORS headers, so the browser cannot fetch it cross-origin. Cached at the
// Next layer for 10 minutes.
export const revalidate = 600

export async function GET() {
  try {
    const res = await fetch('https://cloudless.gr/api/blog?limit=1', {
      next: { revalidate: 600 },
    })
    if (!res.ok) return NextResponse.json({ post: null }, { status: 200 })
    const posts = await res.json()
    const p = Array.isArray(posts) ? posts[0] : null
    if (!p) return NextResponse.json({ post: null })
    return NextResponse.json({
      post: {
        slug: p.slug,
        title: p.title,
        excerpt: p.excerpt,
        date: p.date,
        readTime: p.readTime,
      },
    })
  } catch {
    return NextResponse.json({ post: null })
  }
}
