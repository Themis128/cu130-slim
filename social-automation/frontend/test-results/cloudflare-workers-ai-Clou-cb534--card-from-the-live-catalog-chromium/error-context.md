# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: cloudflare-workers-ai.spec.ts >> Cloudflare UI — live stack @e2e >> AI Providers page renders the Cloudflare card from the live catalog
- Location: tests/cloudflare-workers-ai.spec.ts:270:3

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: page.goto: net::ERR_ABORTED; maybe frame was detached?
Call log:
  - navigating to "http://localhost:3001/settings/ai-providers", waiting until "load"

```

# Test source

```ts
  171 |     const body = await r.json()
  172 |     expect(body.ok).toBe(true)
  173 |     expect(body.response).toEqual(expect.any(String))
  174 |     expect(body.response.length).toBeGreaterThan(0)
  175 |   }, { timeout: 45_000 })
  176 | 
  177 |   test('generate-content with provider=cloudflare runs a real LLM call', async ({ request }) => {
  178 |     const r = await request.post(`${API_V1}/ai/generate-content`, {
  179 |       headers: { ...headers(), 'Content-Type': 'application/json' },
  180 |       data: {
  181 |         prompt: 'Write a one-line tip about productivity for remote workers',
  182 |         platform: 'twitter',
  183 |         provider: 'cloudflare',
  184 |         model: CF_CHAT_MODEL,
  185 |       },
  186 |     })
  187 |     expect(r.status()).toBe(200)
  188 |     const body = await r.json()
  189 |     expect(body.content).toEqual(expect.any(String))
  190 |     expect(body.content.length).toBeGreaterThan(0)
  191 |     expect(Array.isArray(body.hashtags)).toBe(true)
  192 |   }, { timeout: 45_000 })
  193 | 
  194 |   test('Workers AI batch submit → retrieve (queueRequest)', async ({ request }) => {
  195 |     const submitted = await request.post(`${API_V1}/ai/workers-ai/batch`, {
  196 |       headers: { ...headers(), 'Content-Type': 'application/json' },
  197 |       data: {
  198 |         model: CF_EMBED_MODEL,
  199 |         requests: [{ text: 'hello world', external_reference: 'e2e-batch-1' }],
  200 |       },
  201 |     })
  202 |     expect(submitted.status()).toBe(200)
  203 |     const sub = await submitted.json()
  204 |     expect(sub).toMatchObject({
  205 |       request_id: expect.any(String),
  206 |       status: expect.any(String),
  207 |       model: CF_EMBED_MODEL,
  208 |     })
  209 | 
  210 |     const retrieved = await request.post(`${API_V1}/ai/workers-ai/batch/retrieve`, {
  211 |       headers: { ...headers(), 'Content-Type': 'application/json' },
  212 |       data: { model: CF_EMBED_MODEL, request_id: sub.request_id },
  213 |     })
  214 |     expect(retrieved.status()).toBe(200)
  215 |     const ret = await retrieved.json()
  216 |     expect(ret.model).toBe(CF_EMBED_MODEL)
  217 |     expect('status' in ret).toBe(true)
  218 |     expect('responses' in ret).toBe(true)
  219 |   }, { timeout: 60_000 })
  220 | 
  221 |   test('transcribe via real Whisper returns 200 (transcript) or 422 (no speech)', async ({ request }) => {
  222 |     const wav = makeToneWav(1)
  223 |     const r = await request.post(`${API_V1}/ai/transcribe`, {
  224 |       headers: headers(),
  225 |       multipart: {
  226 |         file: { name: 'tone.wav', mimeType: 'audio/wav', buffer: wav },
  227 |         model: '@cf/openai/whisper',
  228 |       },
  229 |     })
  230 |     expect([200, 422]).toContain(r.status())
  231 |     const body = await r.json().catch(() => null)
  232 |     if (r.status() === 200) {
  233 |       expect(body.text).toEqual(expect.any(String))
  234 |     }
  235 |   }, { timeout: 60_000 })
  236 | })
  237 | 
  238 | 
  239 | 
  240 | 
  241 | // ─────────────────────────────────────────────────────────────────────────────
  242 | //  Real browser E2E (Next dev server on :3001 → live social-api backend)
  243 | // ─────────────────────────────────────────────────────────────────────────────
  244 | test.describe('Cloudflare UI — live stack @e2e', () => {
  245 |   test.describe.configure({ mode: 'serial' })
  246 | 
  247 |   test.beforeAll(async ({ request }) => {
  248 |     await registerAndLogin(request)
  249 |   })
  250 | 
  251 |   test.use({ baseURL: 'http://localhost:3001' })
  252 | 
  253 |   test.beforeEach(async ({ page }) => {
  254 |     // Inject auth tokens + tour-completed flag before any JS runs so dashboard
  255 |     // pages never redirect and the onboarding tour never blocks interaction.
  256 |     await page.addInitScript(
  257 |       ([a, r]) => {
  258 |         try {
  259 |           localStorage.setItem('access_token', a as string)
  260 |           localStorage.setItem('refresh_token', r as string)
  261 |           localStorage.setItem('tour_completed', 'true')
  262 |         } catch {
  263 |           /* localStorage unavailable — ignore */
  264 |         }
  265 |       },
  266 |       [accessToken, refreshToken],
  267 |     )
  268 |   })
  269 | 
  270 |   test('AI Providers page renders the Cloudflare card from the live catalog', async ({ page }) => {
> 271 |     await page.goto('/settings/ai-providers')
      |                ^ Error: page.goto: net::ERR_ABORTED; maybe frame was detached?
  272 |     await expect(page).toHaveURL(/\/settings\/ai-providers/)
  273 |     const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
  274 |     await expect(cfCard).toBeVisible()
  275 |     await expect(cfCard.getByRole('button', { name: /browse workers ai models/i })).toBeVisible()
  276 |   })
  277 | 
  278 | 
  279 | 
  280 |   test('Browse Workers AI models: live catalog loads, filters, and picks a model', async ({ page }) => {
  281 |     await page.goto('/settings/ai-providers')
  282 |     const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
  283 | 
  284 |     // Toggle the live Workers AI model browser
  285 |     await cfCard.getByRole('button', { name: /browse workers ai models/i }).click()
  286 |     await expect(page.getByText(/Loading Workers AI catalog/)).toBeVisible()
  287 |     await expect
  288 |       .poll(async () => await cfCard.locator('button.font-mono').count(), { timeout: 30_000 })
  289 |       .toBeGreaterThan(0)
  290 | 
  291 |     // Every rendered row is a real Workers AI model id
  292 |     const firstId = (await cfCard.locator('button.font-mono').first().textContent())?.trim()
  293 |     expect(firstId).toMatch(/^@cf\//)
  294 | 
  295 |     // Search filters the list
  296 |     await page.getByPlaceholder('Search models…').fill('whisper')
  297 |     await expect
  298 |       .poll(async () => {
  299 |         const rows = await cfCard.locator('button.font-mono').allTextContents()
  300 |         return rows.some((t) => t.toLowerCase().includes('whisper'))
  301 |       }, { timeout: 15_000 })
  302 |             .toBe(true)
  303 | 
  304 |     // Pick the first (filtered) model → its id lands in the Default Model input
  305 |     const picked = firstId
  306 |     await cfCard.locator('button.font-mono').first().click()
  307 |     const inputValues = await cfCard
  308 |       .locator('input')
  309 |       .evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value))
  310 |     expect(inputValues).toContain(picked)
  311 |   }, { timeout: 60_000 })
  312 | 
  313 |   test('Save Cloudflare provider → "Provider saved" toast and persistence', async ({ page }) => {
  314 |     await page.goto('/settings/ai-providers')
  315 |     const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
  316 | 
  317 |     // "Enabled" is the first checkbox in the card's toggle row
  318 |     const enabledCheckbox = cfCard.getByRole('checkbox').first()
  319 |     if (!(await enabledCheckbox.isChecked())) await enabledCheckbox.check()
  320 |     const saveBtn = cfCard.getByRole('button', { name: /^save$/i })
  321 |     await expect(saveBtn).toBeVisible()
  322 |     await saveBtn.click()
  323 |     await expect(page.getByText('Provider saved')).toBeVisible({ timeout: 15_000 })
  324 |   }, { timeout: 60_000 })
  325 | 
  326 |     test('Test provider button fires a real Cloudflare connectivity check', async ({ page }) => {
  327 |     await page.goto('/settings/ai-providers')
  328 |     const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
  329 |     await cfCard.getByRole('button', { name: /^test$/i }).click()
  330 |     // Success toast reads: Connected! "<response…>"
  331 |     await expect(page.getByText(/connected!/i)).toBeVisible({ timeout: 45_000 })
  332 |   }, { timeout: 60_000 })
  333 | 
  334 |   test('VoiceRecorder uploads real (fake-device) audio to the live /ai/transcribe endpoint', async ({
  335 |     page,
  336 |     browserName,
  337 |   }) => {
  338 |         // Fake-device microphone capture is Chromium-only for the bundled browsers.
  339 |     test.skip(browserName !== 'chromium', 'fake audio device is Chromium-only')
  340 |     test.use({
  341 |       permissions: ['microphone'],
  342 |       launchOptions: {
  343 |         args: [
  344 |           '--use-fake-device-for-media-stream',
  345 |           '--use-fake-ui-for-media-stream',
  346 |           '--autoplay-policy=user-gesture-required',
  347 |         ],
  348 |       },
  349 |     })
  350 |     test.setTimeout(120_000)
  351 | 
  352 |     const transcribeResp = page.waitForResponse(
  353 |       (r) => r.url().includes('/api/v1/ai/transcribe'),
  354 |       { timeout: 60_000 },
  355 |     )
  356 |     await page.goto('/content/new')
  357 |     // Wait for the content editor to be ready (its placeholder is unique).
  358 |     await expect(page.getByPlaceholder('What do you want to share?')).toBeVisible({ timeout: 20_000 })
  359 | 
  360 |     // The recorder button carries an explicit aria-label.
  361 |     await page.getByRole('button', { name: 'Record and transcribe speech' }).click()
  362 |     // While recording the same button flips to "Stop recording"
  363 |     const stopBtn = page.getByRole('button', { name: 'Stop recording' })
  364 |     await expect(stopBtn).toBeVisible()
  365 |     // Capture a couple seconds of the fake-device tone.
  366 |     await page.waitForTimeout(3000)
  367 |     await stopBtn.click()
  368 | 
  369 |     const resp = await transcribeResp
  370 |     expect([200, 422]).toContain(resp.status())
  371 | 
```