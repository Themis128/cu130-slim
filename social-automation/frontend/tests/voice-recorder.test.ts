import { test, expect, type APIRequestContext } from '@playwright/test'
import { randomUUID } from 'crypto'

const API_URL = process.env.E2E_API_URL ?? 'http://localhost:8083'
const API_V1 = `${API_URL}/api/v1`

const TEST_EMAIL = `vr-e2e-${Date.now()}-${randomUUID().replace(/-/g, '').slice(0, 8)}@example.com`
const TEST_PASSWORD = 'VoiceRecorderE2E!1234'

let accessToken = ''
let refreshToken = ''

async function registerAndLogin(request: APIRequestContext) {
  // A 429 on register means the user was NOT created — retry the whole pair.
  for (let attempt = 0; attempt < 8; attempt++) {
    const reg = await request.post(`${API_V1}/auth/register`, {
      data: { email: TEST_EMAIL, password: TEST_PASSWORD, name: 'VR E2E' },
    })
    if (reg.status() === 429) {
      await new Promise((res) => setTimeout(res, 5000 + 5000 * attempt))
      continue
    }
    const r = await request.post(`${API_V1}/auth/login`, {
      form: { username: TEST_EMAIL, password: TEST_PASSWORD },
    })
    if (r.status() === 429) {
      await new Promise((res) => setTimeout(res, 5000 + 5000 * attempt))
      continue
    }
    const body = await r.json()
    accessToken = body.access_token
    refreshToken = body.refresh_token
    // Fresh users are redirected to /onboarding — mark complete via the real API
    await request.patch(`${API_V1}/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      data: { onboarding_completed: true },
    }).catch(() => {})
    return
  }
  throw new Error('register/login exhausted retries (rate limited)')
}

test.use({
  baseURL: process.env.E2E_FRONTEND_URL ?? 'http://localhost:8082',
  permissions: ['microphone'],
  launchOptions: {
    args: [
      '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream',
      '--autoplay-policy=user-gesture-required',
    ],
  },
})

test.describe('VoiceRecorder — live @e2e', () => {
  test.describe.configure({ mode: 'serial' })
  // Skip before the page fixture is created — Firefox rejects the 'microphone' permission
  test.skip(({ browserName }) => browserName !== 'chromium', 'fake audio device is Chromium-only')

  test.beforeAll(async ({ request }) => {
    // register/login may wait out the auth rate-limit window
    await registerAndLogin(request)
  }, { timeout: 180_000 })

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(
      ([a, r]) => {
        try {
          localStorage.setItem('access_token', a as string)
          localStorage.setItem('refresh_token', r as string)
          localStorage.setItem('tour_completed', 'true')
        } catch {
          /* ignore */
        }
      },
      [accessToken, refreshToken],
    )
  })

  test('VoiceRecorder uploads real (fake-device) audio to the live /ai/transcribe endpoint', async ({
    page,
  }) => {
    test.setTimeout(120_000)

    const transcribeResp = page.waitForResponse(
      (r) => r.url().includes('/api/v1/ai/transcribe'),
      { timeout: 60_000 },
    )
    await page.goto('/content/new')
    await expect(page.getByPlaceholder('What do you want to share?')).toBeVisible({ timeout: 20_000 })

    await page.getByRole('button', { name: 'Record and transcribe speech' }).click()
    const stopBtn = page.getByRole('button', { name: /stop recording/i })
    await expect(stopBtn).toBeVisible({ timeout: 10_000 })

    await page.waitForTimeout(3000)
    await stopBtn.click()

    const resp = await transcribeResp
    expect([200, 422]).toContain(resp.status())

    if (resp.status() === 200) {
      await expect(page.getByText('Transcript added to your post')).toBeVisible({ timeout: 15_000 })
    } else {
      await expect(page.getByText(/no speech detected|Transcription failed/i)).toBeVisible({ timeout: 15_000 })
    }
  }, { retry: 1 })
})
