/**
 * Drive the TikTok OAuth authorize flow headlessly using the stored
 * tiktok.com web session (SocialAccount.meta_data.tiktok_web_cookies).
 *
 * Used for scope upgrades — token refresh can NOT add scopes; only a
 * fresh authorize grants them. TikTok's consent screen offers any scope
 * the client requests (unlisted scopes don't need console approval).
 *
 * Env:
 *   TT_AUTH_URL   — authorize URL from POST /api/v1/accounts/connect
 *   OUT_DIR       — screenshot/dump dir (default /out)
 *   TT_DRY_RUN=1  — stop before clicking the consent button (inspect only)
 *
 * Input: /work/tt_cookies.json — Playwright cookie array (see
 * tiktok-reconnect.sh which exports it from the DB).
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const AUTH_URL = process.env.TT_AUTH_URL;
const DRY_RUN = process.env.TT_DRY_RUN === '1';
const OUT = process.env.OUT_DIR || '/out';
fs.mkdirSync(OUT, { recursive: true });
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});
const log = (o) => console.log(JSON.stringify(o));

if (!AUTH_URL) { console.error('TT_AUTH_URL required'); process.exit(64); }

const cookies = JSON.parse(fs.readFileSync('/work/tt_cookies.json', 'utf8'));

const browser = await chromium.launch({ headless: true, args: ['--disable-blink-features=AutomationControlled'] });
const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
await context.addCookies(cookies);
const page = await context.newPage();

await page.goto(AUTH_URL, { waitUntil: 'domcontentloaded', timeout: 90000 }).catch(() => {});
await page.waitForTimeout(6000);
await shot(page, 'oauth-02-authorize.png');
const consent = await page.evaluate(() => ({
  url: location.href,
  text: document.body.innerText.slice(0, 2500),
  checkboxes: [...document.querySelectorAll('input[type="checkbox"], [role="checkbox"]')].length,
  buttons: [...document.querySelectorAll('button, [role="button"], a')]
    .map((b) => (b.innerText || b.textContent || '').trim()).filter(Boolean).slice(0, 20),
}));
log({ consent });
fs.writeFileSync(path.join(OUT, 'oauth-consent.json'), JSON.stringify(consent, null, 2));

if (DRY_RUN) {
  log({ dryRun: true, note: 'consent screen dumped; no authorize click' });
  await browser.close();
  process.exit(0);
}

for (const name of [/authorize/i, /allow/i, /continue/i, /confirm/i, /同意|授权/]) {
  const b = page.getByRole('button', { name }).first();
  if (await b.isVisible().catch(() => false)) {
    log({ clicking: await b.innerText() });
    await b.click();
    break;
  }
}
const landed = await Promise.race([
  page.waitForURL((u) => String(u).includes('social.cloudless.gr'), { timeout: 45000 }).then(() => 'callback').catch(() => null),
  page.waitForTimeout(30000).then(() => 'timeout'),
]);
await page.waitForTimeout(3000);
await shot(page, 'oauth-03-after.png');
const after = await page.evaluate(() => ({ url: location.href, text: document.body.innerText.slice(0, 800) }));
log({ landed, after });
await browser.close();
process.exit(landed === 'callback' ? 0 : 1);
