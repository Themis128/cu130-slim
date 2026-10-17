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

describe('VoiceRecorder — fallbacks and WAV encoding', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    hoisted.transcribeAudio.mockResolvedValue({ data: { text: 'ok' } })
    installAudioStubs()
  })

  afterEach(() => {
    // @ts-expect-error cleanup
    delete window.MediaRecorder
    // @ts-expect-error cleanup
    delete window.AudioContext
    // @ts-expect-error cleanup
    delete window.webkitAudioContext
    // @ts-expect-error cleanup
    delete window.OfflineAudioContext
    Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
  })

  async function recordAndStop() {
    fireEvent.click(screen.getByRole('button', { name: 'Record and transcribe speech' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Stop recording' }))
  }

  it('records without a mimeType on browsers with no supported codec, via webkitAudioContext, and encodes PCM correctly', async () => {
    const ctorOpts: unknown[] = []
    class NoCodecRecorder extends FakeMediaRecorder {
      static isTypeSupported = vi.fn(() => false)
      constructor(_s: unknown, opts?: unknown) {
        super()
        ctorOpts.push(opts)
      }
    }
    // @ts-expect-error stub
    window.MediaRecorder = NoCodecRecorder
    // @ts-expect-error only the prefixed constructor exists (old Safari)
    window.webkitAudioContext = FakeAudioContext
    // @ts-expect-error remove the standard one
    delete window.AudioContext
    class Rendered extends FakeOfflineAudioContext {
      async startRendering() {
        return { getChannelData: () => new Float32Array([-1, 0.5, 2, -3]) }
      }
    }
    // @ts-expect-error stub
    window.OfflineAudioContext = Rendered

    render(<VoiceRecorder onTranscript={vi.fn()} model="@cf/openai/whisper-tiny-en" />)
    await recordAndStop()
    await waitFor(() => expect(hoisted.transcribeAudio).toHaveBeenCalled())
    expect(ctorOpts).toEqual([undefined])
    const [file, model] = hoisted.transcribeAudio.mock.calls[0] as [File, string]
    expect(model).toBe('@cf/openai/whisper-tiny-en')

    const bytes = await new Promise<ArrayBuffer>((resolve) => {
      const fr = new FileReader()
      fr.onload = () => resolve(fr.result as ArrayBuffer)
      fr.readAsArrayBuffer(file)
    })
    const view = new DataView(bytes)
    const tag = (o: number) => String.fromCharCode(...new Uint8Array(bytes, o, 4))
    expect([tag(0), tag(8), tag(12), tag(36)]).toEqual(['RIFF', 'WAVE', 'fmt ', 'data'])
    expect(view.getUint32(24, true)).toBe(16000) // sample rate
    expect(view.getUint16(22, true)).toBe(1) // mono
    expect(view.getUint32(40, true)).toBe(8) // 4 samples × 2 bytes
    // -1 → -32768, 0.5 → 16383, 2 clamps → 32767, -3 clamps → -32768
    expect([0, 1, 2, 3].map((i) => view.getInt16(44 + i * 2, true))).toEqual([-32768, 16383, 32767, -32768])
  })

  it('refuses to transcribe when nothing was captured', async () => {
    class SilentRecorder extends FakeMediaRecorder {
      start() {
        this.state = 'recording'
        this.ondataavailable?.({ data: new Blob([]) }) // size 0 → dropped
      }
    }
    // @ts-expect-error stub
    window.MediaRecorder = SilentRecorder
    render(<VoiceRecorder onTranscript={vi.fn()} />)
    await recordAndStop()
    await waitFor(() => expect(hoisted.toast.error).toHaveBeenCalledWith('No audio was captured'))
    expect(hoisted.transcribeAudio).not.toHaveBeenCalled()
  })

  it('treats a response without data as no speech', async () => {
    hoisted.transcribeAudio.mockResolvedValue({})
    const onTranscript = vi.fn()
    render(<VoiceRecorder onTranscript={onTranscript} />)
    await recordAndStop()
    await waitFor(() =>
      expect(hoisted.toast.error).toHaveBeenCalledWith('No speech detected — try speaking closer to the mic')
    )
    expect(onTranscript).not.toHaveBeenCalled()
  })

  it('stopping an already-inactive recorder only releases the mic', async () => {
    const stopSpy = vi.fn()
    const track = { stop: vi.fn() }
    class EndedRecorder extends FakeMediaRecorder {
      start() {
        this.state = 'inactive' // device ended the recording on its own
      }
      stop() {
        stopSpy()
      }
    }
    // @ts-expect-error stub
    window.MediaRecorder = EndedRecorder
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [track] })) },
      configurable: true,
    })
    render(<VoiceRecorder onTranscript={vi.fn()} />)
    await recordAndStop()
    expect(stopSpy).not.toHaveBeenCalled()
    expect(track.stop).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Record and transcribe speech' })).toHaveTextContent('Voice')
    expect(hoisted.transcribeAudio).not.toHaveBeenCalled()
  })
})
