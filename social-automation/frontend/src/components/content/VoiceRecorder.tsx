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
export function VoiceRecorder({ onTranscript, model, disabled, className }: VoiceRecorderProps) {
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const mimeTypeRef = useRef('')

  const handleStop = useCallback(async () => {
    const blob = new Blob(chunksRef.current, { type: mimeTypeRef.current || 'audio/webm' })
    if (!blob.size) {
      toast.error('No audio was captured')
      return
    }

    setTranscribing(true)
    try {
      const wav = await toWav16k(blob)
      const file = new File([wav], 'recording.wav', { type: 'audio/wav' })
      const res = await aiApi.transcribeAudio(file, model)
      const text = ((res.data as { text?: string } | undefined)?.text || '').trim()
      if (!text) {
        toast.error('No speech detected — try speaking closer to the mic')
        return
      }
      onTranscript(text)
      toast.success('Transcript added to your post')
    } catch {
      toast.error('Transcription failed — check Cloudflare setup in Settings → AI Providers')
    } finally {
      setTranscribing(false)
    }
  }, [model, onTranscript])

  const startRecording = useCallback(async () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
        toast.error('Audio recording is not supported in this browser')
        return
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream

      const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']
      const mime = candidates.find((c) => MediaRecorder.isTypeSupported(c)) || ''
      mimeTypeRef.current = mime

      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = () => {
        void handleStop()
      }
      recorder.start()
      mediaRecorderRef.current = recorder
      setRecording(true)
    } catch {
      toast.error('Microphone access denied — check browser permissions')
    }
  }, [handleStop])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop()
    }
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    setRecording(false)
  }, [])

  const isBusy = transcribing
  return (
    <div className={className}>
      <Button
        type="button"
        variant={recording ? 'destructive' : 'outline'}
        size="sm"
        onClick={recording ? stopRecording : () => void startRecording()}
        disabled={disabled || isBusy}
        aria-label={recording ? 'Stop recording' : 'Record and transcribe speech'}
        className="gap-1.5"
      >
        {transcribing ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : recording ? (
          <Square className="h-3.5 w-3.5" />
        ) : (
          <Mic className="h-3.5 w-3.5" />
        )}
        {transcribing ? 'Transcribing…' : recording ? 'Stop' : 'Voice'}
      </Button>
    </div>
  )
}
