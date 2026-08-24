'use client'

import { useCallback, useRef, useState } from 'react'
import { Loader2, Mic, Square } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { aiApi } from '@/services/api'
import toast from 'react-hot-toast'

interface VoiceRecorderProps {
  /** Called with the transcribed text once Whisper returns it. */
  onTranscript: (text: string) => void
  /** Optional Workers AI STT model id (defaults to @cf/openai/whisper). */
  model?: string
  disabled?: boolean
  className?: string
}

function writeString(view: DataView, offset: number, str: string) {
  for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i))
}

/**
 * Convert any decodable audio blob to a 16 kHz mono 16-bit PCM WAV file —
 * the format Whisper (and most Workers AI STT models) accepts reliably.
 * Resampling happens automatically through an OfflineAudioContext.
 */
async function toWav16k(blob: Blob): Promise<Blob> {
  const AudioCtor =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
  const ctx = new AudioCtor()
  try {
    const arrayBuffer = await blob.arrayBuffer()
    const audioBuffer = await ctx.decodeAudioData(arrayBuffer)

    const targetRate = 16000
    const numChannels = 1
    const numSamples = Math.max(1, Math.ceil(audioBuffer.duration * targetRate))

    const offline = new OfflineAudioContext(numChannels, numSamples, targetRate)
    const source = offline.createBufferSource()
    source.buffer = audioBuffer
    source.connect(offline.destination)
    source.start(0)
    const rendered = await offline.startRendering()

    const samples = rendered.getChannelData(0)
    const buffer = new ArrayBuffer(44 + samples.length * 2)
    const view = new DataView(buffer)

    writeString(view, 0, 'RIFF')
    view.setUint32(4, 36 + samples.length * 2, true)
    writeString(view, 8, 'WAVE')
    writeString(view, 12, 'fmt ')
    view.setUint32(16, 16, true)
    view.setUint16(20, 1, true) // PCM
    view.setUint16(22, numChannels, true)
    view.setUint32(24, targetRate, true)
    view.setUint32(28, targetRate * numChannels * 2, true)
    view.setUint16(32, numChannels * 2, true)
    view.setUint16(34, 16, true)
    writeString(view, 36, 'data')
    view.setUint32(40, samples.length * 2, true)

    let offset = 44
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]))
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true)
      offset += 2
    }

    return new Blob([buffer], { type: 'audio/wav' })
  } finally {
    void ctx.close()
  }
}