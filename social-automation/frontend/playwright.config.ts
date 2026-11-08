import { defineConfig, devices } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Auto-load admin creds from the repo-root .env (values may be quoted).
// SOCIAL_ADMIN_* -> E2E_ADMIN_*; never overrides an already-set var.
// Lets plain `playwright test` work without the run_pw_e2e.sh wrapper.
const ENV_PATH = path.resolve(import.meta.dirname, '../../.env');
if (fs.existsSync(ENV_PATH)) {
  for (const line of fs.readFileSync(ENV_PATH, 'utf8').split('\n')) {
    const m = line.match(/^\s*(SOCIAL_ADMIN_(?:EMAIL|PASSWORD))\s*=\s*(.*)\s*$/);
    if (!m) continue;
    const key = m[1].replace('SOCIAL_ADMIN', 'E2E_ADMIN');
    const val = m[2].replace(/^["']|["']$/g, '');
    if (val && !process.env[key]) process.env[key] = val;
  }
}

// User-space browser libraries (extracted .deb files — no sudo needed).
// See tests/helpers/playwright-env.sh for setup instructions.
const PLAYWRIGHT_DEPS = '/home/tbaltzakis/.local/lib/playwright-deps';
const EXTRA_LIB_PATH = [
  `${PLAYWRIGHT_DEPS}/usr/lib/x86_64-linux-gnu`,
  `${PLAYWRIGHT_DEPS}/lib/x86_64-linux-gnu`,
].join(':');

// Inject LD_LIBRARY_PATH so Chromium/Firefox can find libnspr4, libnss3, etc.
if (!process.env.LD_LIBRARY_PATH?.includes(PLAYWRIGHT_DEPS)) {
  process.env.LD_LIBRARY_PATH = `${EXTRA_LIB_PATH}:${process.env.LD_LIBRARY_PATH || ''}`;
}

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  testDir: './tests',
  /* Maximum time one test can run for. */
  timeout: 30 * 1000,
  expect: {
    timeout: 10_000,
  },
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI; one local retry too — tests hit the real stack and
     transient 429/slow-render flakes are expected under parallel load. */
  retries: process.env.CI ? 2 : 1,
  /* Cap workers locally — tests hit the real API whose auth endpoints are
     rate-limited (5/min register/login), so unbounded parallelism just
     serializes on 429 backoff anyway. */
  workers: process.env.CI ? 2 : 4,
  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  reporter: 'html',
  /* Shared settings for all the projects below. See https://playwright.dev/docs/test-classes#test-options. */
  use: {
    /* Maximum time each action such as `click()` can take. Defaults to 0 (no limit). */
    actionTimeout: 15 * 1000,
    /* Base URL — points at the real social-frontend container (no dev server). */
    baseURL: process.env.E2E_FRONTEND_URL || 'http://localhost:8082',
    /* Run tests in headless mode */
    headless: true,
    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: 'on-first-retry',
    /* Capture screenshot on failure */
    screenshot: 'only-on-failure',
    /* Pass the environment to launched browser processes — must spread
       process.env or headed runs lose DISPLAY/WAYLAND_DISPLAY. */
    launchOptions: {
      env: {
        ...process.env,
        LD_LIBRARY_PATH: process.env.LD_LIBRARY_PATH || '',
      },
    },
  },

  /* Configure projects for major browsers.
     WebKit is omitted — it needs libgstplay-1.0 which requires sudo to install
     on this Ubuntu. Chromium and Firefox work with user-space extracted libs. */
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
  ],

  /* Folder for test artifacts such as screenshots, videos, traces, etc. */
  // outputDir: 'test-results/',

  /* No webServer block — tests run against the real social-frontend
     container on http://localhost:8082. Start the Docker stack first:
       docker compose up -d social-frontend social-api
  */
});
