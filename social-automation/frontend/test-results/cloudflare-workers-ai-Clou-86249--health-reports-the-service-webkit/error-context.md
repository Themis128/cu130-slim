# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: cloudflare-workers-ai.spec.ts >> Cloudflare backend contract @e2e >> GET /health reports the service
- Location: tests/cloudflare-workers-ai.spec.ts:93:3

# Error details

```
Error: Unexpected register response 500: Internal Server Error
```

# Test source

```ts
  1   | import { test, expect, type APIRequestContext } from '@playwright/test'
  2   | import { randomUUID } from 'crypto';
  3   | 
  4   | /**
  5   |  * Cloudflare Workers AI — full end-to-end coverage with NO mocks.
  6   |  *
  7   |  * These tests talk to the live, dockerized `social-api` backend
  8   |  * (http://localhost:8083/api/v1) which is configured with real
  9   |  * `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` credentials, and to the
  10  |  * live Next.js dev server (port 3001) whose `NEXT_PUBLIC_API_URL` points at
  11  |  * that backend. If the dev server is not already running the Playwright
  12  |  * `webServer` config will boot it automatically.
  13  |  *
  14  |  * Override the backend URL with `E2E_API_URL=http://host:port` if needed.
  15  |  */
  16  | 
  17  | const API_URL = process.env.E2E_API_URL ?? 'http://localhost:8083'
  18  | const API_V1 = `${API_URL}/api/v1`
  19  | const CF_CHAT_MODEL = '@cf/meta/llama-3.1-8b-instruct'
  20  | const CF_EMBED_MODEL = '@cf/baai/bge-m3'
  21  | 
  22  | // A unique test user is registered per worker (Playwright runs workers in parallel).
  23  | const TEST_EMAIL = `cf-e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`
  24  | const TEST_PASSWORD = 'CloudflareE2E!1234'
  25  | 
  26  | let accessToken = ''
  27  | let refreshToken = ''
  28  | 
  29  | /**
  30  |  * Build a 1-second 16 kHz mono 16-bit PCM WAV whose payload is a 440 Hz sine
  31  |  * tone — exactly the format the backend Whisper path expects.
  32  |  */
  33  | function makeToneWav(seconds = 1): Buffer {
  34  |   const sampleRate = 16000
  35  |   const numSamples = Math.max(1, Math.round(seconds * sampleRate))
  36  |   const dataSize = numSamples * 2
  37  |   const buf = Buffer.alloc(44 + dataSize)
  38  |   buf.write('RIFF', 0, 'ascii')
  39  |   buf.writeUInt32LE(36 + dataSize, 4)
  40  |   buf.write('WAVE', 8, 'ascii')
  41  |   buf.write('fmt ', 12, 'ascii')
  42  |   buf.writeUInt32LE(16, 16) // PCM chunk size
  43  |   buf.writeUInt16LE(1, 20) // audio format = PCM
  44  |   buf.writeUInt16LE(1, 22) // mono
  45  |   buf.writeUInt32LE(sampleRate, 24)
  46  |   buf.writeUInt32LE(sampleRate * 2, 28) // byte rate
  47  |   buf.writeUInt16LE(2, 32) // block align
  48  |   buf.writeUInt16LE(16, 34) // bits per sample
  49  |   buf.write('data', 36, 'ascii')
  50  |   buf.writeUInt32LE(dataSize, 40)
  51  |   for (let i = 0; i < numSamples; i++) {
  52  |     const s = Math.round(Math.sin((2 * Math.PI * 440 * i) / sampleRate) * 32767 * 0.5)
  53  |     buf.writeInt16LE(s, 44 + i * 2)
  54  |   }
  55  |   return buf
  56  | }
  57  | 
  58  | /** Register a fresh user and log in, populating the auth-token globals. */
  59  | async function registerAndLogin(request: APIRequestContext) {
  60  |   const reg = await request.post(`${API_V1}/auth/register`, {
  61  |     data: { email: TEST_EMAIL, password: TEST_PASSWORD, name: 'Cloudflare E2E' },
  62  |   })
  63  |   // 400 = email already exists from a prior worker run; that is acceptable.
  64  |   if (!reg.ok() && reg.status() !== 400) {
> 65  |     throw new Error(`Unexpected register response ${reg.status()}: ${reg.statusText()}`)
      |           ^ Error: Unexpected register response 500: Internal Server Error
  66  |   }
  67  |   const login = await request.post(`${API_V1}/auth/login`, {
  68  |     form: { username: TEST_EMAIL, password: TEST_PASSWORD },
  69  |   })
  70  |   expect(login.status(), await login.text()).toBe(200)
  71  |   const tokens = await login.json()
  72  |   accessToken = tokens.access_token
  73  |   refreshToken = tokens.refresh_token
  74  | }
  75  | 
  76  | function headers() {
  77  |   return { Authorization: `Bearer ${accessToken}` }
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
```