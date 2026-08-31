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

// Stable test user — same email across all workers so register is idempotent
// and login always works.  The password never changes during the suite.
export const TEST_USER = {
  email: 'e2e-shared@social-auto.test',
  password: 'E2E-Shared-Pass-123!',
  name: 'E2E Shared Test User',
};

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
}

// Module-level cache so ensureTestUser only hits the API once per process.
let cachedTokens: AuthTokens | null = null;

/**
 * Register the test user (idempotent — 409/422 on duplicate is fine) and
 * return real tokens by logging in.  Safe to call from multiple workers.
 */
export async function ensureTestUser(): Promise<AuthTokens> {
  if (cachedTokens) return cachedTokens;

  // Register (ignore "already exists" errors — any non-2xx is fine here
  // because the user may have been created by a previous run or another worker)
  try {
    await fetch(`${API_BASE}/api/v1/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(TEST_USER),
    });
  } catch {
    // Network errors are non-fatal — the user may already exist
  }

  // Login to get real tokens — retry up to 3 times in case of a race
  // where register hasn't committed yet
  let lastError: Error | null = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          username: TEST_USER.email,
          password: TEST_USER.password,
        }),
      });
      if (res.ok) {
        cachedTokens = (await res.json()) as AuthTokens;
        return cachedTokens;
      }
      lastError = new Error(`Login ${res.status}: ${await res.text()}`);
    } catch (e) {
      lastError = e as Error;
    }
    // Wait before retrying
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(
    `Real login failed for ${TEST_USER.email} after 3 attempts: ${lastError?.message}`
  );
}

/**
 * Inject real tokens into localStorage on the frontend origin so authenticated
 * dashboard pages render without going through the login UI every time.
 */
export async function setAuthCookies(page: Page, tokens: AuthTokens) {
  // Navigate to the frontend origin first so localStorage is scoped correctly
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
