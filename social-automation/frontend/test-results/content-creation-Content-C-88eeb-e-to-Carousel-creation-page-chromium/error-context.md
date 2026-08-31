# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: content-creation.test.ts >> Content Creation Page — real backend >> should navigate to Carousel creation page
- Location: tests/content-creation.test.ts:105:3

# Error details

```
Error: Real login failed for e2e-shared@social-auto.test after 5 attempts: Login 401: {"detail":"Invalid credentials"}
```

# Test source

```ts
  1   | import { test as base, expect, type Page } from '@playwright/test';
  2   | 
  3   | /**
  4   |  * E2E test fixtures that run against the REAL stack:
  5   |  *   - Frontend: http://localhost:8082 (social-frontend container)
  6   |  *   - API:      http://localhost:8083 (social-api container)
  7   |  *
  8   |  * No API mocking. A real test user is registered (idempotent) and logged in
  9   |  * via the actual /api/v1/auth/login endpoint. The resulting tokens are placed
  10  |  * in localStorage so dashboard pages render authenticated.
  11  |  */
  12  | 
  13  | const API_BASE = process.env.E2E_API_URL || 'http://localhost:8083';
  14  | const FRONTEND_BASE = process.env.E2E_FRONTEND_URL || 'http://localhost:8082';
  15  | 
  16  | // Stable test user — same email across all workers so register is idempotent
  17  | // and login always works.  The password never changes during the suite.
  18  | // NOTE: the domain must be a real, non-reserved TLD — Pydantic's EmailStr
  19  | // rejects special-use/reserved TLDs like ".test", ".example", ".invalid".
  20  | export const TEST_USER = {
  21  |   email: 'e2e-shared@socialauto.dev',
  22  |   password: 'E2E-Shared-Pass-123!',
  23  |   name: 'E2E Shared Test User',
  24  | };
  25  | 
  26  | export interface AuthTokens {
  27  |   access_token: string;
  28  |   refresh_token: string;
  29  | }
  30  | 
  31  | // Module-level cache so ensureTestUser only hits the API once per process.
  32  | let cachedTokens: AuthTokens | null = null;
  33  | let registerAttempted = false;
  34  | 
  35  | /**
  36  |  * Register the test user (idempotent — 409/422 on duplicate is fine) and
  37  |  * return real tokens by logging in.  Safe to call from multiple workers.
  38  |  */
  39  | export async function ensureTestUser(): Promise<AuthTokens> {
  40  |   if (cachedTokens) return cachedTokens;
  41  | 
  42  |   // Register only once per process — duplicate-email errors are expected
  43  |   if (!registerAttempted) {
  44  |     registerAttempted = true;
  45  |     try {
  46  |       await fetch(`${API_BASE}/api/v1/auth/register`, {
  47  |         method: 'POST',
  48  |         headers: { 'Content-Type': 'application/json' },
  49  |         body: JSON.stringify(TEST_USER),
  50  |       });
  51  |     } catch {
  52  |       // Network errors are non-fatal — the user may already exist
  53  |     }
  54  |   }
  55  | 
  56  |   // Login to get real tokens — retry with backoff to handle rate limits
  57  |   let lastError: Error | null = null;
  58  |   for (let attempt = 0; attempt < 5; attempt++) {
  59  |     try {
  60  |       const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
  61  |         method: 'POST',
  62  |         headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  63  |         body: new URLSearchParams({
  64  |           username: TEST_USER.email,
  65  |           password: TEST_USER.password,
  66  |         }),
  67  |       });
  68  |       if (res.ok) {
  69  |         cachedTokens = (await res.json()) as AuthTokens;
  70  |         return cachedTokens;
  71  |       }
  72  |       if (res.status === 429) {
  73  |         // Rate limited — wait longer before retrying
  74  |         await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
  75  |         continue;
  76  |       }
  77  |       lastError = new Error(`Login ${res.status}: ${await res.text()}`);
  78  |     } catch (e) {
  79  |       lastError = e as Error;
  80  |     }
> 81  |     await new Promise((r) => setTimeout(r, 500));
      |         ^ Error: Real login failed for e2e-shared@social-auto.test after 5 attempts: Login 401: {"detail":"Invalid credentials"}
  82  |   }
  83  |   throw new Error(
  84  |     `Real login failed for ${TEST_USER.email} after 5 attempts: ${lastError?.message}`
  85  |   );
  86  | }
  87  | 
  88  | /**
  89  |  * Inject real tokens into localStorage on the frontend origin so authenticated
  90  |  * dashboard pages render without going through the login UI every time.
  91  |  */
  92  | export async function setAuthCookies(page: Page, tokens: AuthTokens) {
  93  |   // Navigate to the frontend origin first so localStorage is scoped correctly
  94  |   await page.goto(FRONTEND_BASE + '/login');
  95  |   await page.evaluate(({ access, refresh }) => {
  96  |     localStorage.setItem('access_token', access);
  97  |     localStorage.setItem('refresh_token', refresh);
  98  |   }, { access: tokens.access_token, refresh: tokens.refresh_token });
  99  | }
  100 | 
  101 | type AuthFixture = { authTokens: AuthTokens; authenticatedPage: Page };
  102 | 
  103 | export const test = base.extend<AuthFixture>({
  104 |   authTokens: async ({}, use) => {
  105 |     const tokens = await ensureTestUser();
  106 |     await use(tokens);
  107 |   },
  108 |   authenticatedPage: async ({ page, authTokens }, use) => {
  109 |     await setAuthCookies(page, authTokens);
  110 |     await use(page);
  111 |   },
  112 | });
  113 | 
  114 | export { expect, FRONTEND_BASE, API_BASE };
  115 | 
```