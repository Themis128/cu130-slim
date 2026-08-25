# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: cloudflare-workers-ai.spec.ts >> Cloudflare backend contract @e2e >> generate-content with provider=cloudflare runs a real LLM call
- Location: tests/cloudflare-workers-ai.spec.ts:177:3

# Error details

```
TimeoutError: apiRequestContext.post: Timeout 10000ms exceeded.
Call log:
  - → POST http://localhost:8083/api/v1/ai/generate-content
    - user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.7922.34 Safari/537.36
    - accept: */*
    - accept-encoding: gzip,deflate,br
    - Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3OWU2MzhlNC1lMjc0LTRjMzgtYTVkNS0wNTE1MTNjZGFlOTQiLCJlbWFpbCI6ImNmLWUyZS0xNzg3NjYyNzg1NTIxLTE5MDg1MkBleGFtcGxlLmNvbSIsImV4cCI6MTc4NzY2NDU4OCwidHlwZSI6ImFjY2VzcyJ9.uoHCT53HH6E7zuRXK62g33u78kbhZstaovyE6fCnvPI
    - Content-Type: application/json
    - content-length: 157

```

# Test source

```ts
  78  | }
  79  | 
  80  | 
  81  | // ─────────────────────────────────────────────────────────────────────────────
  82  | //  Live backend contract tests (no browser)
  83  | // ─────────────────────────────────────────────────────────────────────────────
  84  | test.describe('Cloudflare backend contract @e2e', () => {
  85  |   test.describe.configure({ mode: 'serial' })
  86  | 
  87  |   test.beforeAll(async ({ request }) => {
  88  |     await registerAndLogin(request)
  89  |     const h = await request.get(`${API_URL}/health`)
  90  |     expect(h.status()).toBe(200)
  91  |   })
  92  | 
  93  |   test('GET /health reports the service', async ({ request }) => {
  94  |     const r = await request.get(`${API_URL}/health`)
  95  |     expect(await r.json()).toMatchObject({
  96  |       status: 'ok',
  97  |       service: 'Social Automation Platform',
  98  |     })
  99  |   })
  100 | 
  101 |   test('catalog exposes Cloudflare Workers AI with frontend-aligned fields', async ({ request }) => {
  102 |     const r = await request.get(`${API_V1}/ai-providers/catalog`)
  103 |     expect(r.status()).toBe(200)
  104 |     const catalog = await r.json()
  105 |     const cf = catalog.find((c: any) => c.name === 'cloudflare')
  106 |     expect(cf).toBeDefined()
  107 |     // Every field the frontend ModelBrowser + catalog card reads:
  108 |     expect(cf).toMatchObject({
  109 |       name: 'cloudflare',
  110 |       display_name: 'Cloudflare Workers AI',
  111 |       base_url: 'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/',
  112 |       default_model: '@cf/meta/llama-3.1-8b-instruct',
  113 |       requires_key: true,
  114 |       description: expect.any(String),
  115 |       model_examples: expect.any(Array),
  116 |     })
  117 |     expect(cf.model_examples.some((m: string) => m.includes('whisper'))).toBe(true)
  118 |   })
  119 | 
  120 | 
  121 |   test('live Workers AI model listing returns browsable catalog', async ({ request }) => {
  122 |     const r = await request.get(`${API_V1}/ai-providers/cloudflare/models`, { headers: headers() })
  123 |     expect(r.status()).toBe(200)
  124 |     const models = await r.json()
  125 |     expect(Array.isArray(models)).toBe(true)
  126 |     expect(models.length).toBeGreaterThan(5)
  127 |     const keys = new Set(Object.keys(models[0]))
  128 |     expect(keys.has('id')).toBe(true)
  129 |     expect(keys.has('task')).toBe(true)
  130 |     expect(keys.has('description')).toBe(true)
  131 |     expect(models.some((m: any) => m.id.includes('whisper'))).toBe(true)
  132 |   })
  133 | 
  134 |   test('models endpoint requires authentication', async ({ request }) => {
  135 |     const r = await request.get(`${API_V1}/ai-providers/cloudflare/models`)
  136 |     expect([401, 403]).toContain(r.status())
  137 |   })
  138 | 
  139 |   test('upsert + list persist the Cloudflare provider (frontend shape in sync)', async ({ request }) => {
  140 |     const up = await request.put(`${API_V1}/ai-providers/cloudflare`, {
  141 |       headers: headers(),
  142 |       data: { default_model: CF_CHAT_MODEL, is_enabled: true },
  143 |     })
  144 |     expect(up.status()).toBe(200)
  145 |     const saved = await up.json()
  146 |     // AIProviderOut shape expected by the frontend useAIProviders hook/select:
  147 |     expect(saved).toMatchObject({
  148 |       name: 'cloudflare',
  149 |       display_name: 'Cloudflare Workers AI',
  150 |       default_model: CF_CHAT_MODEL,
  151 |       is_enabled: true,
  152 |       is_default: expect.any(Boolean),
  153 |       has_key: expect.any(Boolean),
  154 |       updated_at: expect.any(String),
  155 |     })
  156 |     expect(saved.id).toEqual(expect.any(String))
  157 | 
  158 |     const list = await request.get(`${API_V1}/ai-providers`, { headers: headers() })
  159 |     expect(list.status()).toBe(200)
  160 |     const providers = await list.json()
  161 |     const cf = providers.find((p: any) => p.name === 'cloudflare')
  162 |     expect(cf).toBeDefined()
  163 |     expect(cf.default_model).toBe(CF_CHAT_MODEL)
  164 |   })
  165 | 
  166 |   
  167 |   
  168 |   test('provider test performs a real Cloudflare chat call', async ({ request }) => {
  169 |     const r = await request.post(`${API_V1}/ai-providers/cloudflare/test`, { headers: headers() })
  170 |     expect(r.status()).toBe(200)
  171 |     const body = await r.json()
  172 |     expect(body.ok).toBe(true)
  173 |     expect(body.response).toEqual(expect.any(String))
  174 |     expect(body.response.length).toBeGreaterThan(0)
  175 |   }, { timeout: 45_000 })
  176 | 
  177 |   test('generate-content with provider=cloudflare runs a real LLM call', async ({ request }) => {
> 178 |     const r = await request.post(`${API_V1}/ai/generate-content`, {
      |                             ^ TimeoutError: apiRequestContext.post: Timeout 10000ms exceeded.
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
  271 |     await page.goto('/settings/ai-providers')
  272 |     await expect(page).toHaveURL(/\/settings\/ai-providers/)
  273 |     const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
  274 |     await expect(cfCard).toBeVisible()
  275 |     await expect(cfCard.getByRole('button', { name: /browse workers ai models/i })).toBeVisible()
  276 |   })
  277 | 
  278 | 
```