import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const hoisted = vi.hoisted(() => ({
  transcribeAudio: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('@/services/api', () => ({ aiApi: { transcribeAudio: hoisted.transcribeAudio } }))
vi.mock('react-hot-toast', () => ({ default: hoisted.toast }))

import { VoiceRecorder } from './VoiceRecorder'

class FakeMediaRecorder {
  static isTypeSupported = vi.fn(() => true)
  state = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  start() {
    this.state = 'recording'
    this.ondataavailable?.({ data: new Blob(['audio'], { type: 'audio/webm' }) })
  }
  stop() {
    this.state = 'inactive'
    this.onstop?.()
  }
}

const fakeStream = { getTracks: () => [{ stop: vi.fn() }] }

class FakeAudioContext {
  decodeAudioData = vi.fn(async () => ({ duration: 0.01 }))
  close = vi.fn(async () => undefined)
}

class FakeOfflineAudioContext {
  constructor(_ch: number, _len: number, _rate: number) {}
  destination = {}
  createBufferSource() {
    return { buffer: null as unknown, connect: vi.fn(), start: vi.fn() }
  }
  async startRendering() {
    return { getChannelData: () => new Float32Array(160) }
  }
}

function installAudioStubs() {
  // @ts-expect-error jsdom lacks these
  window.MediaRecorder = FakeMediaRecorder
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia: vi.fn(async () => fakeStream) },
    configurable: true,
  })
  // @ts-expect-error stub
  window.AudioContext = FakeAudioContext
  // @ts-expect-error stub
  window.OfflineAudioContext = FakeOfflineAudioContext
}

describe('VoiceRecorder', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    hoisted.transcribeAudio.mockResolvedValue({ data: { text: 'hello world' } })
  })

  afterEach(() => {
    // @ts-expect-error cleanup
    delete window.MediaRecorder
    // @ts-expect-error cleanup
    delete window.AudioContext
    // @ts-expect-error cleanup
    delete window.OfflineAudioContext
    Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
  })

  it('shows unsupported-browser error when MediaRecorder is missing', async () => {
    render(<VoiceRecorder onTranscript={vi.fn()} />)
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() =>
      expect(hoisted.toast.error).toHaveBeenCalledWith('Audio recording is not supported in this browser')
    )
  })

  it('surfaces microphone-denied errors', async () => {
    installAudioStubs()
    ;(navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('denied'))
    render(<VoiceRecorder onTranscript={vi.fn()} />)
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() =>
      expect(hoisted.toast.error).toHaveBeenCalledWith('Microphone access denied — check browser permissions')
    )
  })

  it('records, converts to WAV, and calls onTranscript with the text', async () => {
    installAudioStubs()
    const onTranscript = vi.fn()
    render(<VoiceRecorder onTranscript={onTranscript} />)

    fireEvent.click(screen.getByRole('button', { name: 'Record and transcribe speech' }))
    await screen.findByRole('button', { name: 'Stop recording' })
    fireEvent.click(screen.getByRole('button', { name: 'Stop recording' }))

    await waitFor(() => expect(hoisted.transcribeAudio).toHaveBeenCalled())
    const [file, model] = hoisted.transcribeAudio.mock.calls[0]
    expect((file as File).name).toBe('recording.wav')
    expect((file as File).type).toBe('audio/wav')
    expect(model).toBeUndefined()

    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('hello world'))
    expect(hoisted.toast.success).toHaveBeenCalledWith('Transcript added to your post')
  })

  it('reports empty transcripts as no speech detected', async () => {
    installAudioStubs()
    hoisted.transcribeAudio.mockResolvedValue({ data: { text: '  ' } })
    render(<VoiceRecorder onTranscript={vi.fn()} />)
    fireEvent.click(screen.getByRole('button'))
    fireEvent.click(await screen.findByRole('button', { name: 'Stop recording' }))
    await waitFor(() =>
      expect(hoisted.toast.error).toHaveBeenCalledWith('No speech detected — try speaking closer to the mic')
    )
  })

  it('reports transcription failures', async () => {
    installAudioStubs()
    hoisted.transcribeAudio.mockRejectedValue(new Error('503'))
    render(<VoiceRecorder onTranscript={vi.fn()} />)
    fireEvent.click(screen.getByRole('button'))
    fireEvent.click(await screen.findByRole('button', { name: 'Stop recording' }))
    await waitFor(() =>
      expect(hoisted.toast.error).toHaveBeenCalledWith('Transcription failed — check Cloudflare setup in Settings → AI Providers')
    )
  })
})
