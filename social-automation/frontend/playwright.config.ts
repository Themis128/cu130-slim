import { defineConfig, devices } from '@playwright/test';

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
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI for stability, but use more workers locally */
  workers: process.env.CI ? 2 : undefined,
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
    /* Pass the LD_LIBRARY_PATH to launched browser processes */
    launchOptions: {
      env: {
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
