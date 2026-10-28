'use client'

import { isSafeVideoUrl } from '@/lib/utils'

interface SafeVideoProps {
  src: string | null | undefined
  className?: string
  muted?: boolean
  loop?: boolean
  autoPlay?: boolean
  playsInline?: boolean
}

export function SafeVideo({
  src,
  className,
  muted,
  loop,
  autoPlay,
  playsInline,
}: SafeVideoProps) {
  if (!isSafeVideoUrl(src)) {
    return null
  }

  return (
    <video
      src={src}
      className={className}
      muted={muted}
      loop={loop}
      autoPlay={autoPlay}
      playsInline={playsInline}
    />
  )
}
