# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: cloudflare-workers-ai.spec.ts >> Cloudflare backend contract @e2e >> provider test performs a real Cloudflare chat call
- Location: tests/cloudflare-workers-ai.spec.ts:167:3

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: true
Received: false
```

# Test source

```ts
  71  |   accessToken = tokens.access_token
  72  |   refreshToken = tokens.refresh_token
  73  | }
  74  | 
  75  | function headers() {
  76  |   return { Authorization: `Bearer ${accessToken}` }
  77  | }
  78  | 
  79  | 
  80  | // ─────────────────────────────────────────────────────────────────────────────
  81  | //  Live backend contract tests (no browser)
  82  | // ─────────────────────────────────────────────────────────────────────────────
  83  | test.describe('Cloudflare backend contract @e2e', () => {
  84  |   test.describe.configure({ mode: 'serial' })
  85  | 
  86  |   test.beforeAll(async ({ request }) => {
  87  |     await registerAndLogin(request)
  88  |     const h = await request.get(`${API_URL}/health`)
  89  |     expect(h.status()).toBe(200)
  90  |   })
  91  | 
  92  |   test('GET /health reports the service', async ({ request }) => {
  93  |     const r = await request.get(`${API_URL}/health`)
  94  |     expect(await r.json()).toMatchObject({
  95  |       status: 'ok',
  96  |       service: 'Social Automation Platform',
  97  |     })
  98  |   })
  99  | 
  100 |   test('catalog exposes Cloudflare Workers AI with frontend-aligned fields', async ({ request }) => {
  101 |     const r = await request.get(`${API_V1}/ai-providers/catalog`)
  102 |     expect(r.status()).toBe(200)
  103 |     const catalog = await r.json()
  104 |     const cf = catalog.find((c: any) => c.name === 'cloudflare')
  105 |     expect(cf).toBeDefined()
  106 |     // Every field the frontend ModelBrowser + catalog card reads:
  107 |     expect(cf).toMatchObject({
  108 |       name: 'cloudflare',
  109 |       display_name: 'Cloudflare Workers AI',
  110 |       base_url: 'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/',
  111 |       default_model: '@cf/meta/llama-3.1-8b-instruct',
  112 |       requires_key: true,
  113 |       description: expect.any(String),
  114 |       model_examples: expect.any(Array),
  115 |     })
  116 |     expect(cf.model_examples.some((m: string) => m.includes('whisper'))).toBe(true)
  117 |   })
  118 | 
  119 | 
  120 |   test('live Workers AI model listing returns browsable catalog', async ({ request }) => {
  121 |     const r = await request.get(`${API_V1}/ai-providers/cloudflare/models`, { headers: headers() })
  122 |     expect(r.status()).toBe(200)
  123 |     const models = await r.json()
  124 |     expect(Array.isArray(models)).toBe(true)
  125 |     expect(models.length).toBeGreaterThan(5)
  126 |     const keys = new Set(Object.keys(models[0]))
  127 |     expect(keys.has('id')).toBe(true)
  128 |     expect(keys.has('task')).toBe(true)
  129 |     expect(keys.has('description')).toBe(true)
  130 |     expect(models.some((m: any) => m.id.includes('whisper'))).toBe(true)
  131 |   })
  132 | 
  133 |   test('models endpoint requires authentication', async ({ request }) => {
  134 |     const r = await request.get(`${API_V1}/ai-providers/cloudflare/models`)
  135 |     expect([401, 403]).toContain(r.status())
  136 |   })
  137 | 
  138 |   test('upsert + list persist the Cloudflare provider (frontend shape in sync)', async ({ request }) => {
  139 |     const up = await request.put(`${API_V1}/ai-providers/cloudflare`, {
  140 |       headers: headers(),
  141 |       data: { default_model: CF_CHAT_MODEL, is_enabled: true },
  142 |     })
  143 |     expect(up.status()).toBe(200)
  144 |     const saved = await up.json()
  145 |     // AIProviderOut shape expected by the frontend useAIProviders hook/select:
  146 |     expect(saved).toMatchObject({
  147 |       name: 'cloudflare',
  148 |       display_name: 'Cloudflare Workers AI',
  149 |       default_model: CF_CHAT_MODEL,
  150 |       is_enabled: true,
  151 |       is_default: expect.any(Boolean),
  152 |       has_key: expect.any(Boolean),
  153 |       updated_at: expect.any(String),
  154 |     })
  155 |     expect(saved.id).toEqual(expect.any(String))
  156 | 
  157 |     const list = await request.get(`${API_V1}/ai-providers`, { headers: headers() })
  158 |     expect(list.status()).toBe(200)
  159 |     const providers = await list.json()
  160 |     const cf = providers.find((p: any) => p.name === 'cloudflare')
  161 |     expect(cf).toBeDefined()
  162 |     expect(cf.default_model).toBe(CF_CHAT_MODEL)
  163 |   })
  164 | 
  165 |   
  166 |   
  167 |   test('provider test performs a real Cloudflare chat call', async ({ request }) => {
  168 |     const r = await request.post(`${API_V1}/ai-providers/cloudflare/test`, { headers: headers() })
  169 |     expect(r.status()).toBe(200)
  170 |     const body = await r.json()
> 171 |     expect(body.ok).toBe(true)
      |                     ^ Error: expect(received).toBe(expected) // Object.is equality
  172 |     expect(body.response).toEqual(expect.any(String))
  173 |     expect(body.response.length).toBeGreaterThan(0)
  174 |   }, { timeout: 45_000 })
  175 | 
  176 |   test('generate-content with provider=cloudflare runs a real LLM call', async ({ request }) => {
  177 |     const r = await request.post(`${API_V1}/ai/generate-content`, {
  178 |       headers: { ...headers(), 'Content-Type': 'application/json' },
  179 |       data: {
  180 |         prompt: 'Write a one-line tip about productivity for remote workers',
  181 |         platform: 'twitter',
  182 |         provider: 'cloudflare',
  183 |         model: CF_CHAT_MODEL,
  184 |       },
  185 |     })
  186 |     expect(r.status()).toBe(200)
  187 |     const body = await r.json()
  188 |     expect(body.content).toEqual(expect.any(String))
  189 |     expect(body.content.length).toBeGreaterThan(0)
  190 |     expect(Array.isArray(body.hashtags)).toBe(true)
  191 |   }, { timeout: 45_000 })
  192 | 
  193 |   test('Workers AI batch submit → retrieve (queueRequest)', async ({ request }) => {
  194 |     const submitted = await request.post(`${API_V1}/ai/workers-ai/batch`, {
  195 |       headers: { ...headers(), 'Content-Type': 'application/json' },
  196 |       data: {
  197 |         model: CF_EMBED_MODEL,
  198 |         requests: [{ text: 'hello world', external_reference: 'e2e-batch-1' }],
  199 |       },
  200 |     })
  201 |     expect(submitted.status()).toBe(200)
  202 |     const sub = await submitted.json()
  203 |     expect(sub).toMatchObject({
  204 |       request_id: expect.any(String),
  205 |       status: expect.any(String),
  206 |       model: CF_EMBED_MODEL,
  207 |     })
  208 | 
  209 |     const retrieved = await request.post(`${API_V1}/ai/workers-ai/batch/retrieve`, {
  210 |       headers: { ...headers(), 'Content-Type': 'application/json' },
  211 |       data: { model: CF_EMBED_MODEL, request_id: sub.request_id },
  212 |     })
  213 |     expect(retrieved.status()).toBe(200)
  214 |     const ret = await retrieved.json()
  215 |     expect(ret.model).toBe(CF_EMBED_MODEL)
  216 |     expect('status' in ret).toBe(true)
  217 |     expect('responses' in ret).toBe(true)
  218 |   }, { timeout: 60_000 })
  219 | 
  220 |   test('transcribe via real Whisper returns 200 (transcript) or 422 (no speech)', async ({ request }) => {
  221 |     const wav = makeToneWav(1)
  222 |     const r = await request.post(`${API_V1}/ai/transcribe`, {
  223 |       headers: headers(),
  224 |       multipart: {
  225 |         file: { name: 'tone.wav', mimeType: 'audio/wav', buffer: wav },
  226 |         model: '@cf/openai/whisper',
  227 |       },
  228 |     })
  229 |     expect([200, 422]).toContain(r.status())
  230 |     const body = await r.json().catch(() => null)
  231 |     if (r.status() === 200) {
  232 |       expect(body.text).toEqual(expect.any(String))
  233 |     }
  234 |   }, { timeout: 60_000 })
  235 | })
  236 | 
  237 | 
  238 | 
  239 | 
  240 | // ─────────────────────────────────────────────────────────────────────────────
  241 | //  Real browser E2E (Next dev server on :3001 → live social-api backend)
  242 | // ─────────────────────────────────────────────────────────────────────────────
  243 | test.describe('Cloudflare UI — live stack @e2e', () => {
  244 |   test.describe.configure({ mode: 'serial' })
  245 | 
  246 |   test.beforeAll(async ({ request }) => {
  247 |     await registerAndLogin(request)
  248 |   })
  249 | 
  250 |   test.use({ baseURL: 'http://localhost:3001' })
  251 | 
  252 |   test.beforeEach(async ({ page }) => {
  253 |     // Inject auth tokens + tour-completed flag before any JS runs so dashboard
  254 |     // pages never redirect and the onboarding tour never blocks interaction.
  255 |     await page.addInitScript(
  256 |       ([a, r]) => {
  257 |         try {
  258 |           localStorage.setItem('access_token', a as string)
  259 |           localStorage.setItem('refresh_token', r as string)
  260 |           localStorage.setItem('tour_completed', 'true')
  261 |         } catch {
  262 |           /* localStorage unavailable — ignore */
  263 |         }
  264 |       },
  265 |       [accessToken, refreshToken],
  266 |     )
  267 |   })
  268 | 
  269 |   test('AI Providers page renders the Cloudflare card from the live catalog', async ({ page }) => {
  270 |     await page.goto('/settings/ai-providers')
  271 |     await expect(page).toHaveURL(/\/settings\/ai-providers/)
```