import { isSafeImageUrl } from '@/lib/utils'

export function SafeImage({
  src,
  alt,
  className,
}: {
  src: string | null | undefined
  alt?: string
  className?: string
}) {
  if (!isSafeImageUrl(src)) return null
  return <img src={src} alt={alt || ''} className={className} />
}
