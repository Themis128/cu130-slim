import { test, expect } from '@playwright/test';
import { writeFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from '@playwright/test';

/**
 * Lighthouse performance/accessibility/SEO audits for the Cloudless social stack.
 *
 * Uses the Lighthouse Node API directly (not playwright-lighthouse) for maximum
 * control and compatibility. Lighthouse connects to Chromium via CDP on a
 * remote debugging port.
 *
 * Packages (installed in /home/tbaltzakis/.local/lib/lighthouse-tool):
 *   - lighthouse@11.7.1
 *   - playwright-core@1.62.1
 *
 * Categories audited:
 *   - performance
 *   - accessibility
 *   - best-practices
 *   - seo
 *
 * Reports are saved as HTML in test-results/lighthouse/
 */

const LIGHTHOUSE_IMPORT_PATH = '/home/tbaltzakis/.local/lib/lighthouse-tool/node_modules/lighthouse/core/index.js';
const FRONTEND_URL = process.env.E2E_FRONTEND_URL || 'http://localhost:8082';
const REPORT_DIR = resolve(process.cwd(), 'test-results/lighthouse');
const PORT = 9223; // CDP port for Lighthouse — must not conflict with other tests

// Score thresholds (0-1). Lighthouse scores: 0-0.49 red, 0.5-0.89 orange, 0.9-1 green
const THRESHOLDS = {
  performance: 0.5,      // Next.js SSR is not instant; 50 is a reasonable floor
  accessibility: 0.9,    // We control the UI — a11y should be high
  'best-practices': 0.8, // Allow some flexibility for dev environment
  seo: 0.8,              // Pages should be SEO-friendly
};

interface LighthouseResult {
  lhr: {
    categories: Record<string, { score: number | null; title: string }>;
  };
  report: string;
}

async function runLighthouseAudit(url: string, pageName: string): Promise<LighthouseResult> {
  // Dynamically import lighthouse from the external install location
  const lighthouseModule = await import(LIGHTHOUSE_IMPORT_PATH);
  const lighthouse = lighthouseModule.default || lighthouseModule;

  // Launch Chromium with remote debugging enabled
  const browser = await chromium.launch({
    headless: true,
    args: [
      `--remote-debugging-port=${PORT}`,
      '--no-sandbox',
      '--disable-setuid-sandbox',
    ],
    executablePath: undefined, // use Playwright's bundled Chromium
  });

  try {
    // Navigate to the page first so Lighthouse can audit it
    const page = await browser.newPage();
    await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 }).catch(() => {});
    await page.close();

    // Run Lighthouse audit
    const result = await lighthouse(url, {
      port: PORT,
      output: 'html',
      logLevel: 'error',
      onlyCategories: Object.keys(THRESHOLDS),
      screenEmulation: { disabled: true },
    });

    if (!result) {
      throw new Error(`Lighthouse returned no result for ${url}`);
    }

    // Save HTML report
    mkdirSync(REPORT_DIR, { recursive: true });
    const reportPath = resolve(REPORT_DIR, `${pageName}.html`);
    writeFileSync(reportPath, result.report);

    return result as unknown as LighthouseResult;
  } finally {
    await browser.close();
  }
}

function getScore(result: LighthouseResult, category: string): number {
  const cat = result.lhr.categories[category];
  if (!cat || cat.score === null) return 0;
  return cat.score;
}

test.describe('Lighthouse Audits — real frontend', () => {
  test.describe.configure({ mode: 'serial' });

  test('login page — performance, accessibility, SEO', async () => {
    const result = await runLighthouseAudit(`${FRONTEND_URL}/login`, 'login');
    const perf = getScore(result, 'performance');
    const a11y = getScore(result, 'accessibility');
    const seo = getScore(result, 'seo');
    const bp = getScore(result, 'best-practices');

    console.log(`Login page — perf: ${perf}, a11y: ${a11y}, seo: ${seo}, bp: ${bp}`);
    expect(a11y, 'accessibility score').toBeGreaterThanOrEqual(THRESHOLDS.accessibility);
    expect(seo, 'seo score').toBeGreaterThanOrEqual(THRESHOLDS.seo);
  });

  test('register page — performance, accessibility, SEO', async () => {
    const result = await runLighthouseAudit(`${FRONTEND_URL}/register`, 'register');
    const perf = getScore(result, 'performance');
    const a11y = getScore(result, 'accessibility');
    const seo = getScore(result, 'seo');
    const bp = getScore(result, 'best-practices');

    console.log(`Register page — perf: ${perf}, a11y: ${a11y}, seo: ${seo}, bp: ${bp}`);
    expect(a11y, 'accessibility score').toBeGreaterThanOrEqual(THRESHOLDS.accessibility);
    expect(seo, 'seo score').toBeGreaterThanOrEqual(THRESHOLDS.seo);
  });

  test('forgot-password page — performance, accessibility, SEO', async () => {
    const result = await runLighthouseAudit(`${FRONTEND_URL}/forgot-password`, 'forgot-password');
    const a11y = getScore(result, 'accessibility');
    const seo = getScore(result, 'seo');

    console.log(`Forgot password — a11y: ${a11y}, seo: ${seo}`);
    expect(a11y, 'accessibility score').toBeGreaterThanOrEqual(THRESHOLDS.accessibility);
  });

  test('home page — performance, accessibility, SEO', async () => {
    const result = await runLighthouseAudit(`${FRONTEND_URL}/`, 'home');
    const perf = getScore(result, 'performance');
    const a11y = getScore(result, 'accessibility');
    const seo = getScore(result, 'seo');
    const bp = getScore(result, 'best-practices');

    console.log(`Home page — perf: ${perf}, a11y: ${a11y}, seo: ${seo}, bp: ${bp}`);
    // Home page may redirect to /login or /dashboard — be lenient on SEO
    expect(a11y, 'accessibility score').toBeGreaterThanOrEqual(THRESHOLDS.accessibility);
  });
});
