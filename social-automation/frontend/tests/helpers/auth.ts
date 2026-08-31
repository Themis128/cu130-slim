import { test as base, expect, type Page } from '@playwright/test';

/**
 * E2E test fixtures that run against the REAL stack:
 *   - Frontend: http://localhost:8082 (social-frontend container)
 *   - API:      http://localhost:8083 (social-api container)
 *
 * No API mocking. A real test user is registered (idempotent) and logged in
 * via the actual /api/v1/auth/login endpoint. The resulting tokens are placed
 * in localStorage so dashboard pages render authenticated.
 */

const API_BASE = process.env.E2E_API_URL || 'http://localhost:8083';
const FRONTEND_BASE = process.env.E2E_FRONTEND_URL || 'http://localhost:8082';

export const TEST_USER = {
  email: `e2e-${process.pid}@social-auto.test`,
  password: 'E2E-Test-Pass-123!',
  name: 'E2E Test User',
};

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
}

/**
 * Register the test user (idempotent — 409/422 on duplicate is fine) and
 * return real tokens by logging in. Runs once per worker via the fixture.
 */
export async function ensureTestUser(): Promise<AuthTokens> {
  // Register (ignore "already exists" errors)
  await fetch(`${API_BASE}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(TEST_USER),
  }).catch(() => {});

  // Login to get real tokens
  const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      username: TEST_USER.email,
      password: TEST_USER.password,
    }),
  });

  if (!res.ok) {
    throw new Error(`Real login failed for ${TEST_USER.email}: ${res.status} ${await res.text()}`);
  }
  return (await res.json()) as AuthTokens;
}

/**
 * Inject real tokens into localStorage on the frontend origin so authenticated
 * dashboard pages render without going through the login UI every time.
 */
export async function setAuthCookies(page: Page, tokens: AuthTokens) {
  await page.goto(FRONTEND_BASE + '/login');
  await page.evaluate(({ access, refresh }) => {
    localStorage.setItem('access_token', access);
    localStorage.setItem('refresh_token', refresh);
  }, { access: tokens.access_token, refresh: tokens.refresh_token });
}

type AuthFixture = { authTokens: AuthTokens; authenticatedPage: Page };

export const test = base.extend<AuthFixture>({
  authTokens: async ({}, use) => {
    const tokens = await ensureTestUser();
    await use(tokens);
  },
  authenticatedPage: async ({ page, authTokens }, use) => {
    await setAuthCookies(page, authTokens);
    await use(page);
  },
});

export { expect, FRONTEND_BASE, API_BASE };
