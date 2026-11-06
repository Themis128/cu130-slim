import { test, expect, ensureTestUser } from './helpers/auth'

let accessToken = ''
let refreshToken = ''

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

  test.beforeAll(async () => {
    // Shared cached user — avoids extra register/login calls that compete
    // with the 5/min auth rate limit under parallel workers.
    const tokens = await ensureTestUser()
    accessToken = tokens.access_token
    refreshToken = tokens.refresh_token
  })

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
      // Real Whisper via Cloudflare — cold starts can take 30-60s.
      { timeout: 90_000 },
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

    // A 200 with empty text (fake-device tone → no speech) shows the
    // 'No speech detected' toast; a 422 surfaces 'Transcription failed'.
    // All prove the live round-trip without crashing.
    await expect(
      page.getByText(/Transcript added to your post|No speech detected|Transcription failed/i),
    ).toBeVisible({ timeout: 45_000 })
  })
})
