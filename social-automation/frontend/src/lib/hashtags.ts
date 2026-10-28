// Storage convention is bare tag names ("cloudless") — renderers add the '#'.
// Older rows and AI-generated payloads may carry the prefix itself, which
// renders as '##cloudless' if joined naively.

export const normalizeTag = (raw: string): string =>
  String(raw).trim().replace(/^#+/, '').trim()

export const normalizeTagList = (tags: string[]): string[] => {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of tags || []) {
    const t = normalizeTag(raw)
    if (t && !seen.has(t.toLowerCase())) {
      seen.add(t.toLowerCase())
      out.push(t)
    }
  }
  return out
}
