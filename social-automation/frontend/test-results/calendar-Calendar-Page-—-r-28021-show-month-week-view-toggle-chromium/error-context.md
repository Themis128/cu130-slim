# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: calendar.test.ts >> Calendar Page — real backend >> should show month/week view toggle
- Location: tests/calendar.test.ts:135:3

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
  18  | export const TEST_USER = {
  19  |   email: 'e2e-shared@social-auto.test',
  20  |   password: 'E2E-Shared-Pass-123!',
  21  |   name: 'E2E Shared Test User',
  22  | };
  23  | 
  24  | export interface AuthTokens {
  25  |   access_token: string;
  26  |   refresh_token: string;
  27  | }
  28  | 
  29  | // Module-level cache so ensureTestUser only hits the API once per process.
  30  | let cachedTokens: AuthTokens | null = null;
  31  | let registerAttempted = false;
  32  | 
  33  | /**
  34  |  * Register the test user (idempotent — 409/422 on duplicate is fine) and
  35  |  * return real tokens by logging in.  Safe to call from multiple workers.
  36  |  */
  37  | export async function ensureTestUser(): Promise<AuthTokens> {
  38  |   if (cachedTokens) return cachedTokens;
  39  | 
  40  |   // Register only once per process — duplicate-email errors are expected
  41  |   if (!registerAttempted) {
  42  |     registerAttempted = true;
  43  |     try {
  44  |       await fetch(`${API_BASE}/api/v1/auth/register`, {
  45  |         method: 'POST',
  46  |         headers: { 'Content-Type': 'application/json' },
  47  |         body: JSON.stringify(TEST_USER),
  48  |       });
  49  |     } catch {
  50  |       // Network errors are non-fatal — the user may already exist
  51  |     }
  52  |   }
  53  | 
  54  |   // Login to get real tokens — retry with backoff to handle rate limits
  55  |   let lastError: Error | null = null;
  56  |   for (let attempt = 0; attempt < 5; attempt++) {
  57  |     try {
  58  |       const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
  59  |         method: 'POST',
  60  |         headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  61  |         body: new URLSearchParams({
  62  |           username: TEST_USER.email,
  63  |           password: TEST_USER.password,
  64  |         }),
  65  |       });
  66  |       if (res.ok) {
  67  |         cachedTokens = (await res.json()) as AuthTokens;
  68  |         return cachedTokens;
  69  |       }
  70  |       if (res.status === 429) {
  71  |         // Rate limited — wait longer before retrying
  72  |         await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
  73  |         continue;
  74  |       }
  75  |       lastError = new Error(`Login ${res.status}: ${await res.text()}`);
  76  |     } catch (e) {
  77  |       lastError = e as Error;
  78  |     }
  79  |     await new Promise((r) => setTimeout(r, 500));
  80  |   }
> 81  |   throw new Error(
      |         ^ Error: Real login failed for e2e-shared@social-auto.test after 5 attempts: Login 401: {"detail":"Invalid credentials"}
  82  |     `Real login failed for ${TEST_USER.email} after 5 attempts: ${lastError?.message}`
  83  |   );
  84  | }
  85  | 
  86  | /**
  87  |  * Inject real tokens into localStorage on the frontend origin so authenticated
  88  |  * dashboard pages render without going through the login UI every time.
  89  |  */
  90  | export async function setAuthCookies(page: Page, tokens: AuthTokens) {
  91  |   // Navigate to the frontend origin first so localStorage is scoped correctly
  92  |   await page.goto(FRONTEND_BASE + '/login');
  93  |   await page.evaluate(({ access, refresh }) => {
  94  |     localStorage.setItem('access_token', access);
  95  |     localStorage.setItem('refresh_token', refresh);
  96  |   }, { access: tokens.access_token, refresh: tokens.refresh_token });
  97  | }
  98  | 
  99  | type AuthFixture = { authTokens: AuthTokens; authenticatedPage: Page };
  100 | 
  101 | export const test = base.extend<AuthFixture>({
  102 |   authTokens: async ({}, use) => {
  103 |     const tokens = await ensureTestUser();
  104 |     await use(tokens);
  105 |   },
  106 |   authenticatedPage: async ({ page, authTokens }, use) => {
  107 |     await setAuthCookies(page, authTokens);
  108 |     await use(page);
  109 |   },
  110 | });
  111 | 
  112 | export { expect, FRONTEND_BASE, API_BASE };
  113 | 
```