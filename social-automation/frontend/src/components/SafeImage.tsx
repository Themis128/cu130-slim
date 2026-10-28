'use client'

import { isSafeImageUrl } from '@/lib/utils'

interface SafeImageProps {
  src: string | null | undefined
  alt: string
  className?: string
  width?: number
  height?: number
}

export function SafeImage({
  src,
  alt,
  className,
  width,
  height,
}: SafeImageProps) {
  if (!isSafeImageUrl(src)) {
    return null
  }

  return <img src={src} alt={alt} className={className} width={width} height={height} />
}
