import { test, expect } from './helpers/auth';

// Routes swept for console errors, page errors, and failed API calls.
const ROUTES = [
  '/dashboard',
  '/content',
  '/content/new',
  '/content/linkedin',
  '/content/article/new',
  '/content/carousel/new',
  '/content/poll/new',
  '/content/story/new',
  '/content/thread/new',
  '/calendar',
  '/media',
  '/media/generate',
  '/workflows',
  '/brand',
  '/brand/assets',
  '/brand/autopilot',
  '/brand/competitors',
  '/brand/guidelines',
  '/brand/health',
  '/brand/identity',
  '/brand/monitoring',
  '/brand/visual',
  '/brand/voice',
  '/card',
  '/accounts',
  '/inbox',
  '/messenger',
  '/whatsapp',
  '/telegram',
  '/tiktok',
  '/team',
  '/browser-login',
  '/mcp-stack',
  '/analytics',
  '/settings',
  '/settings/ai-providers',
  '/settings/ai-providers/usage',
  '/settings/audit-logs',
  '/settings/billing',
];

// Benign noise we don't fail on
const IGNORED_CONSOLE = [
  /Download the React DevTools/i,
  /third-party cookie/i,
  /favicon/i,
  /preload/i,
  /hydration/i,
  /deprecat/i,
];
const IGNORED_REQUEST = [
  /favicon/,
  /_next\/static/,
  /sockjs|webpack-hmr|_next\/webpack/,
  /api\/v1\/auth\/login/,
  /api\/v1\/auth\/register/,
];

// Endpoints where 404 is the documented "empty state" contract (the page
// renders a create-first/empty UI). Resource-404 console noise from these
// is expected, not a bug.
const EMPTY_STATE_404 = [/api\/v1\/brand(\/|$)/];

test.describe('Page audit — every dashboard route', () => {
  for (const route of ROUTES) {
    test(`${route} loads without errors`, async ({ authenticatedPage: page }) => {
      const consoleErrors: string[] = [];
      const pageErrors: string[] = [];
      const failedRequests: string[] = [];
      let emptyState404s = 0;

      page.on('console', (msg) => {
        if (msg.type() !== 'error') return;
        const text = msg.text();
        if (IGNORED_CONSOLE.some((re) => re.test(text))) return;
        consoleErrors.push(text.slice(0, 300));
      });
      page.on('pageerror', (err) => pageErrors.push(String(err).slice(0, 300)));
      page.on('response', (res) => {
        const url = res.url();
        if (IGNORED_REQUEST.some((re) => re.test(url))) return;
        if (res.status() >= 500) {
          failedRequests.push(`${res.status()} ${url}`.slice(0, 300));
        } else if (res.status() === 404 && EMPTY_STATE_404.some((re) => re.test(url))) {
          emptyState404s += 1;
        }
      });

      const resp = await page.goto(route, { waitUntil: 'domcontentloaded' });
      expect(resp?.status(), `${route} returned ${resp?.status()}`).toBeLessThan(500);
      // Let client-side fetches settle
      await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});
      await page.waitForTimeout(1500);

      // No unhandled page errors or 5xx API responses
      expect(pageErrors, `${route} pageerrors`).toEqual([]);
      expect(failedRequests, `${route} failed API calls`).toEqual([]);
      // Discount "Failed to load resource" console noise produced by
      // documented empty-state 404s (e.g. no brand yet).
      const resource404 = /Failed to load resource:.*404/;
      let discounted = emptyState404s;
      const realConsoleErrors = consoleErrors.filter((text) => {
        if (discounted > 0 && resource404.test(text)) {
          discounted -= 1;
          return false;
        }
        return true;
      });
      expect(realConsoleErrors, `${route} console errors`).toEqual([]);
    });
  }
});
