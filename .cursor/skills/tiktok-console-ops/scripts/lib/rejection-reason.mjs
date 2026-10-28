/**
 * Login, open Cloudless pending app, click "See why" / Review comments, dump rejection text.
 * Does not submit for review.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = process.env.TIKTOK_APP_ID || '7630494700880906241';
fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismissCookies(page) {
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll('button, a, [role="button"]')];
    const t = btns.find((b) => /allow all/i.test(b.textContent || ''));
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
const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();

await page.goto('https://developers.tiktok.com/login/', { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(1200);
await dismissCookies(page);
await page.getByPlaceholder('Email').fill(EMAIL);
await page.getByPlaceholder('Password').fill(PASSWORD);
await page.waitForTimeout(400);
await dismissCookies(page);
const loginBtn = page.getByRole('button', { name: /^Log in$/i });
for (let i = 0; i < 20; i++) {
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
  write('rejection-login.json', { ok: false, error: 'login_failed', url: page.url() });
  console.log(JSON.stringify({ ok: false, error: 'login_failed' }));
  await browser.close();
  process.exit(2);
}

const urls = [
  `https://developers.tiktok.com/app/${APP_ID}/pending`,
  `https://developers.tiktok.com/app/${APP_ID}`,
];
for (const u of urls) {
  await page.goto(u, { waitUntil: 'domcontentloaded', timeout: 120000 }).catch(() => {});
  await page.waitForTimeout(4000);
  await dismissCookies(page);
  if (/Cloudless|Not approved|App review/i.test(await page.locator('body').innerText())) break;
}

await shot(page, 'rejection-before.png');
const before = await page.locator('body').innerText();

// Click "See why" if present
const seeWhy = page.getByText(/See why/i).first();
let clickedSeeWhy = false;
if (await seeWhy.isVisible().catch(() => false)) {
  await seeWhy.click({ force: true }).catch(() => {});
  clickedSeeWhy = true;
  await page.waitForTimeout(2500);
  await dismissCookies(page);
}

// Also try Review comments tab
const reviewTab = page.getByText(/Review comments/i).first();
let clickedReview = false;
if (await reviewTab.isVisible().catch(() => false)) {
  await reviewTab.click({ force: true }).catch(() => {});
  clickedReview = true;
  await page.waitForTimeout(2000);
}

await shot(page, 'rejection-after.png');
const after = await page.locator('body').innerText();

// Collect dialog / modal text
const dialogText = await page.evaluate(() => {
  const nodes = [
    ...document.querySelectorAll('[role="dialog"], .ant-modal, .modal, [class*="Modal"], [class*="Drawer"]'),
  ];
  return nodes.map((n) => (n.innerText || '').trim()).filter(Boolean).slice(0, 5);
});

const rejectionHints = [];
for (const chunk of [after, ...dialogText]) {
  const lines = chunk.split('\n').map((l) => l.trim()).filter(Boolean);
  for (let i = 0; i < lines.length; i++) {
    if (/reject|not approved|see why|reason|guideline|demo video|scope|sandbox/i.test(lines[i])) {
      rejectionHints.push(lines.slice(Math.max(0, i - 1), i + 6).join(' | '));
    }
  }
}

const report = {
  ok: true,
  url: page.url(),
  clickedSeeWhy,
  clickedReview,
  dialogText,
  rejectionHints: [...new Set(rejectionHints)].slice(0, 40),
  beforeSnippet: before.slice(0, 4000),
  afterSnippet: after.slice(0, 12000),
};
write('rejection-reason.json', report);
console.log(JSON.stringify({
  ok: true,
  url: report.url,
  clickedSeeWhy,
  clickedReview,
  dialogCount: dialogText.length,
  hintCount: report.rejectionHints.length,
  hints: report.rejectionHints.slice(0, 15),
}, null, 2));
await browser.close();
