/**
 * Set TikTok Cloudless Web/Desktop Website URL to https://cloudless.gr
 * (fixes production rejection). Leaves OAuth redirect unchanged.
 * Does not submit for review.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = process.env.TIKTOK_APP_ID || '7630494700880906241';
const WEB_URL = 'https://cloudless.gr';
const REDIRECT = 'https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback';

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismissCookies(page) {
  await page.evaluate(() => {
    const t = [...document.querySelectorAll('button')].find((b) => /allow all/i.test(b.textContent || ''));
    if (t) t.click();
  }).catch(() => {});
  await page.waitForTimeout(600);
}

if (!EMAIL || !PASSWORD) {
  console.error('Missing TIKTOK_DEV_EMAIL / TIKTOK_DEV_PASSWORD');
  process.exit(1);
}

const browser = await chromium.launch({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled'],
});
const page = await (await browser.newContext({ viewport: { width: 1440, height: 1100 } })).newPage();

await page.goto('https://developers.tiktok.com/login/', { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(1200);
await dismissCookies(page);
await page.getByPlaceholder('Email').fill(EMAIL);
await page.getByPlaceholder('Password').fill(PASSWORD);
await page.waitForTimeout(400);
await dismissCookies(page);
const loginBtn = page.getByRole('button', { name: /^Log in$/i });
for (let i = 0; i < 25; i++) {
  if (!(await loginBtn.isDisabled().catch(() => true))) break;
  await page.waitForTimeout(200);
}
await loginBtn.click({ force: true });
await Promise.race([
  page.waitForURL((u) => !String(u).includes('/login'), { timeout: 45000 }).catch(() => {}),
  page.waitForTimeout(20000),
]);
await dismissCookies(page);
if (page.url().includes('/login')) {
  write('fix-website-url.json', { ok: false, error: 'login_failed' });
  console.log(JSON.stringify({ ok: false, error: 'login_failed' }));
  await browser.close();
  process.exit(2);
}

await page.goto(`https://developers.tiktok.com/app/${APP_ID}/pending`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(4500);
await dismissCookies(page);
await shot(page, 'fix-web-01-before.png');

const inputs = page.locator('input[type="text"], input[type="url"], input:not([type])');
const n = await inputs.count();
let updated = 0;
const before = [];
const after = [];
for (let i = 0; i < n; i++) {
  const el = inputs.nth(i);
  if (!(await el.isVisible().catch(() => false))) continue;
  if (await el.isDisabled().catch(() => true)) continue;
  const cur = ((await el.inputValue().catch(() => '')) || '').trim();
  before.push(cur);
  // Website URL only — not OAuth callback
  if (/^https?:\/\/(www\.)?social\.cloudless\.gr\/?$/i.test(cur)
    || /^https?:\/\/(www\.)?(social\.)?cloudless\.(jp|app)\/?$/i.test(cur)
    || cur === '') {
    // Only fill empty if nearby label suggests website — skip empty to avoid wrong field
    if (!cur) continue;
    await el.fill(WEB_URL);
    updated += 1;
    after.push({ from: cur, to: WEB_URL });
  }
}

// If already cloudless.gr, note it
const values = [];
for (let i = 0; i < n; i++) {
  const el = inputs.nth(i);
  if (!(await el.isVisible().catch(() => false))) continue;
  values.push({
    disabled: await el.isDisabled().catch(() => false),
    value: (await el.inputValue().catch(() => '')) || '',
  });
}

let saved = false;
const saveBtn = page.getByRole('button', { name: /save|apply|update/i }).first();
if (updated > 0 && await saveBtn.count() && (await saveBtn.isEnabled().catch(() => false))) {
  await saveBtn.click({ force: true });
  await page.waitForTimeout(3000);
  saved = true;
}
await dismissCookies(page);
await shot(page, 'fix-web-02-after.png');

const hasWeb = values.some((v) => /^https?:\/\/(www\.)?cloudless\.gr\/?$/i.test(v.value.trim()))
  || after.some((a) => a.to === WEB_URL);
const hasRedirect = values.some((v) => (v.value || '').includes(REDIRECT));

const report = {
  ok: true,
  url: page.url(),
  updated,
  saved,
  after,
  hasWebUrlCloudlessGr: hasWeb,
  hasRedirectOk: hasRedirect,
  visibleInputs: values.filter((v) => v.value).slice(0, 20),
};
write('fix-website-url.json', report);
console.log(JSON.stringify({
  ok: true,
  updated,
  saved,
  hasWebUrlCloudlessGr: hasWeb,
  hasRedirectOk: hasRedirect,
  changes: after,
}, null, 2));
await browser.close();
