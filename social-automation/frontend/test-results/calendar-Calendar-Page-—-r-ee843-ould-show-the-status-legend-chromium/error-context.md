# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: calendar.test.ts >> Calendar Page — real backend >> should show the status legend
- Location: tests/calendar.test.ts:122:3

# Error details

```
Error: Real login failed for e2e-38981@social-auto.test: 429 {"error":"Rate limit exceeded: 10 per 1 minute"}
```

# Test source

```ts
  1  | import { test as base, expect, type Page } from '@playwright/test';
  2  | 
  3  | /**
  4  |  * E2E test fixtures that run against the REAL stack:
  5  |  *   - Frontend: http://localhost:8082 (social-frontend container)
  6  |  *   - API:      http://localhost:8083 (social-api container)
  7  |  *
  8  |  * No API mocking. A real test user is registered (idempotent) and logged in
  9  |  * via the actual /api/v1/auth/login endpoint. The resulting tokens are placed
  10 |  * in localStorage so dashboard pages render authenticated.
  11 |  */
  12 | 
  13 | const API_BASE = process.env.E2E_API_URL || 'http://localhost:8083';
  14 | const FRONTEND_BASE = process.env.E2E_FRONTEND_URL || 'http://localhost:8082';
  15 | 
  16 | export const TEST_USER = {
  17 |   email: `e2e-${process.pid}@social-auto.test`,
  18 |   password: 'E2E-Test-Pass-123!',
  19 |   name: 'E2E Test User',
  20 | };
  21 | 
  22 | export interface AuthTokens {
  23 |   access_token: string;
  24 |   refresh_token: string;
  25 | }
  26 | 
  27 | /**
  28 |  * Register the test user (idempotent — 409/422 on duplicate is fine) and
  29 |  * return real tokens by logging in. Runs once per worker via the fixture.
  30 |  */
  31 | export async function ensureTestUser(): Promise<AuthTokens> {
  32 |   // Register (ignore "already exists" errors)
  33 |   await fetch(`${API_BASE}/api/v1/auth/register`, {
  34 |     method: 'POST',
  35 |     headers: { 'Content-Type': 'application/json' },
  36 |     body: JSON.stringify(TEST_USER),
  37 |   }).catch(() => {});
  38 | 
  39 |   // Login to get real tokens
  40 |   const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
  41 |     method: 'POST',
  42 |     headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  43 |     body: new URLSearchParams({
  44 |       username: TEST_USER.email,
  45 |       password: TEST_USER.password,
  46 |     }),
  47 |   });
  48 | 
  49 |   if (!res.ok) {
> 50 |     throw new Error(`Real login failed for ${TEST_USER.email}: ${res.status} ${await res.text()}`);
     |           ^ Error: Real login failed for e2e-38981@social-auto.test: 429 {"error":"Rate limit exceeded: 10 per 1 minute"}
  51 |   }
  52 |   return (await res.json()) as AuthTokens;
  53 | }
  54 | 
  55 | /**
  56 |  * Inject real tokens into localStorage on the frontend origin so authenticated
  57 |  * dashboard pages render without going through the login UI every time.
  58 |  */
  59 | export async function setAuthCookies(page: Page, tokens: AuthTokens) {
  60 |   await page.goto(FRONTEND_BASE + '/login');
  61 |   await page.evaluate(({ access, refresh }) => {
  62 |     localStorage.setItem('access_token', access);
  63 |     localStorage.setItem('refresh_token', refresh);
  64 |   }, { access: tokens.access_token, refresh: tokens.refresh_token });
  65 | }
  66 | 
  67 | type AuthFixture = { authTokens: AuthTokens; authenticatedPage: Page };
  68 | 
  69 | export const test = base.extend<AuthFixture>({
  70 |   authTokens: async ({}, use) => {
  71 |     const tokens = await ensureTestUser();
  72 |     await use(tokens);
  73 |   },
  74 |   authenticatedPage: async ({ page, authTokens }, use) => {
  75 |     await setAuthCookies(page, authTokens);
  76 |     await use(page);
  77 |   },
  78 | });
  79 | 
  80 | export { expect, FRONTEND_BASE, API_BASE };
  81 | 
```