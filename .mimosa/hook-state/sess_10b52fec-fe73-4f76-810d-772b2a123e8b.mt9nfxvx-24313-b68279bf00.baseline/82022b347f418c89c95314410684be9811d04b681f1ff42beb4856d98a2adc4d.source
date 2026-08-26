import { test, expect, type APIRequestContext } from '@playwright/test'
import { randomUUID } from 'crypto';

/**
 * Cloudflare Workers AI — full end-to-end coverage with NO mocks.
 *
 * These tests talk to the live, dockerized `social-api` backend
 * (http://localhost:8083/api/v1) which is configured with real
 * `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` credentials, and to the
 * live Next.js dev server (port 3001) whose `NEXT_PUBLIC_API_URL` points at
 * that backend. If the dev server is not already running the Playwright
 * `webServer` config will boot it automatically.
 *
 * Override the backend URL with `E2E_API_URL=http://host:port` if needed.
 */

const API_URL = process.env.E2E_API_URL ?? 'http://localhost:8083'
const API_V1 = `${API_URL}/api/v1`
const CF_CHAT_MODEL = '@cf/meta/llama-3.1-8b-instruct'
const CF_EMBED_MODEL = '@cf/baai/bge-m3'

// A unique test user is registered per worker (Playwright runs workers in parallel).
const TEST_EMAIL = `cf-e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`
const TEST_PASSWORD = 'CloudflareE2E!1234'

let accessToken = ''
let refreshToken = ''

/**
 * Build a 1-second 16 kHz mono 16-bit PCM WAV whose payload is a 440 Hz sine
 * tone — exactly the format the backend Whisper path expects.
 */
function makeToneWav(seconds = 1): Buffer {
  const sampleRate = 16000
  const numSamples = Math.max(1, Math.round(seconds * sampleRate))
  const dataSize = numSamples * 2
  const buf = Buffer.alloc(44 + dataSize)
  buf.write('RIFF', 0, 'ascii')
  buf.writeUInt32LE(36 + dataSize, 4)
  buf.write('WAVE', 8, 'ascii')
  buf.write('fmt ', 12, 'ascii')
  buf.writeUInt32LE(16, 16) // PCM chunk size
  buf.writeUInt16LE(1, 20) // audio format = PCM
  buf.writeUInt16LE(1, 22) // mono
  buf.writeUInt32LE(sampleRate, 24)
  buf.writeUInt32LE(sampleRate * 2, 28) // byte rate
  buf.writeUInt16LE(2, 32) // block align
  buf.writeUInt16LE(16, 34) // bits per sample
  buf.write('data', 36, 'ascii')
  buf.writeUInt32LE(dataSize, 40)
  for (let i = 0; i < numSamples; i++) {
    const s = Math.round(Math.sin((2 * Math.PI * 440 * i) / sampleRate) * 32767 * 0.5)
    buf.writeInt16LE(s, 44 + i * 2)
  }
  return buf
}

/** Register a fresh user and log in, populating the auth-token globals. */
async function registerAndLogin(request: APIRequestContext) {
  const reg = await request.post(`${API_V1}/auth/register`, {
    data: { email: TEST_EMAIL, password: TEST_PASSWORD, name: 'Cloudflare E2E' },
  })
  // 400 = email already exists from a prior worker run; that is acceptable.
  if (!reg.ok() && reg.status() !== 400) {
    throw new Error(`Unexpected register response ${reg.status()}: ${reg.statusText()}`)
  }
  const login = await request.post(`${API_V1}/auth/login`, {
    form: { username: TEST_EMAIL, password: TEST_PASSWORD },
  })
  expect(login.status(), await login.text()).toBe(200)
  const tokens = await login.json()
  accessToken = tokens.access_token
  refreshToken = tokens.refresh_token
}

function headers() {
  return { Authorization: `Bearer ${accessToken}` }
}


// ─────────────────────────────────────────────────────────────────────────────
//  Live backend contract tests (no browser)
// ─────────────────────────────────────────────────────────────────────────────
test.describe('Cloudflare backend contract @e2e', () => {
  test.describe.configure({ mode: 'serial' })

  test.beforeAll(async ({ request }) => {
    await registerAndLogin(request)
    const h = await request.get(`${API_URL}/health`)
    expect(h.status()).toBe(200)
  })

  test('GET /health reports the service', async ({ request }) => {
    const r = await request.get(`${API_URL}/health`)
    expect(await r.json()).toMatchObject({
      status: 'ok',
      service: 'Social Automation Platform',
    })
  })

  test('catalog exposes Cloudflare Workers AI with frontend-aligned fields', async ({ request }) => {
    const r = await request.get(`${API_V1}/ai-providers/catalog`)
    expect(r.status()).toBe(200)
    const catalog = await r.json()
    const cf = catalog.find((c: any) => c.name === 'cloudflare')
    expect(cf).toBeDefined()
    // Every field the frontend ModelBrowser + catalog card reads:
    expect(cf).toMatchObject({
      name: 'cloudflare',
      display_name: 'Cloudflare Workers AI',
      base_url: 'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/',
      default_model: '@cf/meta/llama-3.1-8b-instruct',
      requires_key: true,
      description: expect.any(String),
      model_examples: expect.any(Array),
    })
    expect(cf.model_examples.some((m: string) => m.includes('whisper'))).toBe(true)
  })


  test('live Workers AI model listing returns browsable catalog', async ({ request }) => {
    const r = await request.get(`${API_V1}/ai-providers/cloudflare/models`, { headers: headers() })
    expect(r.status()).toBe(200)
    const models = await r.json()
    expect(Array.isArray(models)).toBe(true)
    expect(models.length).toBeGreaterThan(5)
    const keys = new Set(Object.keys(models[0]))
    expect(keys.has('id')).toBe(true)
    expect(keys.has('task')).toBe(true)
    expect(keys.has('description')).toBe(true)
    expect(models.some((m: any) => m.id.includes('whisper'))).toBe(true)
  })

  test('models endpoint requires authentication', async ({ request }) => {
    const r = await request.get(`${API_V1}/ai-providers/cloudflare/models`)
    expect([401, 403]).toContain(r.status())
  })

  test('upsert + list persist the Cloudflare provider (frontend shape in sync)', async ({ request }) => {
    const up = await request.put(`${API_V1}/ai-providers/cloudflare`, {
      headers: headers(),
      data: { default_model: CF_CHAT_MODEL, is_enabled: true },
    })
    expect(up.status()).toBe(200)
    const saved = await up.json()
    // AIProviderOut shape expected by the frontend useAIProviders hook/select:
    expect(saved).toMatchObject({
      name: 'cloudflare',
      display_name: 'Cloudflare Workers AI',
      default_model: CF_CHAT_MODEL,
      is_enabled: true,
      is_default: expect.any(Boolean),
      has_key: expect.any(Boolean),
      updated_at: expect.any(String),
    })
    expect(saved.id).toEqual(expect.any(String))

    const list = await request.get(`${API_V1}/ai-providers`, { headers: headers() })
    expect(list.status()).toBe(200)
    const providers = await list.json()
    const cf = providers.find((p: any) => p.name === 'cloudflare')
    expect(cf).toBeDefined()
    expect(cf.default_model).toBe(CF_CHAT_MODEL)
  })

  
  
  test('provider test performs a real Cloudflare chat call', async ({ request }) => {
    const r = await request.post(`${API_V1}/ai-providers/cloudflare/test`, { headers: headers() })
    expect(r.status()).toBe(200)
    const body = await r.json()
    expect(body.ok).toBe(true)
    expect(body.response).toEqual(expect.any(String))
    expect(body.response.length).toBeGreaterThan(0)
  }, { timeout: 45_000 })

  test('generate-content with provider=cloudflare runs a real LLM call', async ({ request }) => {
    const r = await request.post(`${API_V1}/ai/generate-content`, {
      headers: { ...headers(), 'Content-Type': 'application/json' },
      data: {
        prompt: 'Write a one-line tip about productivity for remote workers',
        platform: 'twitter',
        provider: 'cloudflare',
        model: CF_CHAT_MODEL,
      },
    })
    expect(r.status()).toBe(200)
    const body = await r.json()
    expect(body.content).toEqual(expect.any(String))
    expect(body.content.length).toBeGreaterThan(0)
    expect(Array.isArray(body.hashtags)).toBe(true)
  }, { timeout: 45_000 })

  test('Workers AI batch submit → retrieve (queueRequest)', async ({ request }) => {
    const submitted = await request.post(`${API_V1}/ai/workers-ai/batch`, {
      headers: { ...headers(), 'Content-Type': 'application/json' },
      data: {
        model: CF_EMBED_MODEL,
        requests: [{ text: 'hello world', external_reference: 'e2e-batch-1' }],
      },
    })
    expect(submitted.status()).toBe(200)
    const sub = await submitted.json()
    expect(sub).toMatchObject({
      request_id: expect.any(String),
      status: expect.any(String),
      model: CF_EMBED_MODEL,
    })

    const retrieved = await request.post(`${API_V1}/ai/workers-ai/batch/retrieve`, {
      headers: { ...headers(), 'Content-Type': 'application/json' },
      data: { model: CF_EMBED_MODEL, request_id: sub.request_id },
    })
    expect(retrieved.status()).toBe(200)
    const ret = await retrieved.json()
    expect(ret.model).toBe(CF_EMBED_MODEL)
    expect('status' in ret).toBe(true)
    expect('responses' in ret).toBe(true)
  }, { timeout: 60_000 })

  test('transcribe via real Whisper returns 200 (transcript) or 422 (no speech)', async ({ request }) => {
    const wav = makeToneWav(1)
    const r = await request.post(`${API_V1}/ai/transcribe`, {
      headers: headers(),
      multipart: {
        file: { name: 'tone.wav', mimeType: 'audio/wav', buffer: wav },
        model: '@cf/openai/whisper',
      },
    })
    expect([200, 422]).toContain(r.status())
    const body = await r.json().catch(() => null)
    if (r.status() === 200) {
      expect(body.text).toEqual(expect.any(String))
    }
  }, { timeout: 60_000 })
})




// ─────────────────────────────────────────────────────────────────────────────
//  Real browser E2E (Next dev server on :3001 → live social-api backend)
// ─────────────────────────────────────────────────────────────────────────────
test.describe('Cloudflare UI — live stack @e2e', () => {
  test.describe.configure({ mode: 'serial' })

  test.beforeAll(async ({ request }) => {
    await registerAndLogin(request)
  })

  test.use({ baseURL: 'http://localhost:3001' })

  test.beforeEach(async ({ page }) => {
    // Inject auth tokens + tour-completed flag before any JS runs so dashboard
    // pages never redirect and the onboarding tour never blocks interaction.
    await page.addInitScript(
      ([a, r]) => {
        try {
          localStorage.setItem('access_token', a as string)
          localStorage.setItem('refresh_token', r as string)
          localStorage.setItem('tour_completed', 'true')
        } catch {
          /* localStorage unavailable — ignore */
        }
      },
      [accessToken, refreshToken],
    )
  })

  test('AI Providers page renders the Cloudflare card from the live catalog', async ({ page }) => {
    await page.goto('/settings/ai-providers')
    await expect(page).toHaveURL(/\/settings\/ai-providers/)
    const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
    await expect(cfCard).toBeVisible()
    await expect(cfCard.getByRole('button', { name: /browse workers ai models/i })).toBeVisible()
  })



  test('Browse Workers AI models: live catalog loads, filters, and picks a model', async ({ page }) => {
    await page.goto('/settings/ai-providers')
    const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()

    // Toggle the live Workers AI model browser
    await cfCard.getByRole('button', { name: /browse workers ai models/i }).click()
    await expect(page.getByText(/Loading Workers AI catalog/)).toBeVisible()
    await expect
      .poll(async () => await cfCard.locator('button.font-mono').count(), { timeout: 30_000 })
      .toBeGreaterThan(0)

    // Every rendered row is a real Workers AI model id
    const firstId = (await cfCard.locator('button.font-mono').first().textContent())?.trim()
    expect(firstId).toMatch(/^@cf\//)

    // Search filters the list
    await page.getByPlaceholder('Search models…').fill('whisper')
    await expect
      .poll(async () => {
        const rows = await cfCard.locator('button.font-mono').allTextContents()
        return rows.some((t) => t.toLowerCase().includes('whisper'))
      }, { timeout: 15_000 })
            .toBe(true)

    // Pick the first (filtered) model → its id lands in the Default Model input
    const picked = firstId
    await cfCard.locator('button.font-mono').first().click()
    const inputValues = await cfCard
      .locator('input')
      .evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value))
    expect(inputValues).toContain(picked)
  }, { timeout: 60_000 })

  test('Save Cloudflare provider → "Provider saved" toast and persistence', async ({ page }) => {
    await page.goto('/settings/ai-providers')
    const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()

    // "Enabled" is the first checkbox in the card's toggle row
    const enabledCheckbox = cfCard.getByRole('checkbox').first()
    if (!(await enabledCheckbox.isChecked())) await enabledCheckbox.check()
    const saveBtn = cfCard.getByRole('button', { name: /^save$/i })
    await expect(saveBtn).toBeVisible()
    await saveBtn.click()
    await expect(page.getByText('Provider saved')).toBeVisible({ timeout: 15_000 })
  }, { timeout: 60_000 })

    test('Test provider button fires a real Cloudflare connectivity check', async ({ page }) => {
    await page.goto('/settings/ai-providers')
    const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
    await cfCard.getByRole('button', { name: /^test$/i }).click()
    // Success toast reads: Connected! "<response…>"
    await expect(page.getByText(/connected!/i)).toBeVisible({ timeout: 45_000 })
  }, { timeout: 60_000 })

  test('VoiceRecorder uploads real (fake-device) audio to the live /ai/transcribe endpoint', async ({
    page,
    browserName,
  }) => {
        // Fake-device microphone capture is Chromium-only for the bundled browsers.
    test.skip(browserName !== 'chromium', 'fake audio device is Chromium-only')
    test.use({
      permissions: ['microphone'],
      launchOptions: {
        args: [
          '--use-fake-device-for-media-stream',
          '--use-fake-ui-for-media-stream',
          '--autoplay-policy=user-gesture-required',
        ],
      },
    })
    test.setTimeout(120_000)

    const transcribeResp = page.waitForResponse(
      (r) => r.url().includes('/api/v1/ai/transcribe'),
      { timeout: 60_000 },
    )
    await page.goto('/content/new')
    // Wait for the content editor to be ready (its placeholder is unique).
    await expect(page.getByPlaceholder('What do you want to share?')).toBeVisible({ timeout: 20_000 })

    // The recorder button carries an explicit aria-label.
    await page.getByRole('button', { name: 'Record and transcribe speech' }).click()
    // While recording the same button flips to "Stop recording"
    const stopBtn = page.getByRole('button', { name: 'Stop recording' })
    await expect(stopBtn).toBeVisible()
    // Capture a couple seconds of the fake-device tone.
    await page.waitForTimeout(3000)
    await stopBtn.click()

    const resp = await transcribeResp
    expect([200, 422]).toContain(resp.status())

    if (resp.status() === 200) {
      // Real Whisper output — transcript injected into the editor + success toast.
      await expect(page.getByText('Transcript added to your post')).toBeVisible({ timeout: 15_000 })
    } else {
      // Empty/inaudible tone → backend 422, frontend must NOT crash.
      await expect(page.getByText(/no speech detected|Transcription failed/i)).toBeVisible({ timeout: 15_000 })
    }
  }, { retry: 1 })
})



