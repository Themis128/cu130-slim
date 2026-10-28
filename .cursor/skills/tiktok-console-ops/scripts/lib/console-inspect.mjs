/**
 * Login to TikTok developer portal, open Cloudless app, dump state for agents.
 * Fixes cookie banner before interactions. Does not submit audit.
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
    const btns = [...document.querySelectorAll('button')];
    const t = btns.find((b) => /allow all/i.test(b.textContent || ''));
    if (t) t.click();
  }).catch(() => {});
  await page.waitForTimeout(800);
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
await page.waitForTimeout(1500);
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
await shot(page, 'inspect-after-login.png');

if (page.url().includes('/login')) {
  write('inspect-login.json', { ok: false, url: page.url(), error: 'still_on_login' });
  console.log(JSON.stringify({ ok: false, error: 'login_failed' }));
  await browser.close();
  process.exit(2);
}

await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
}).catch(() => page.goto('https://developers.tiktok.com/apps/'));
await page.waitForTimeout(5000);
await dismissCookies(page);
await shot(page, 'inspect-app.png');

const text = await page.locator('body').innerText();
const domainToken = (text.match(/tiktok-domain-verification=[A-Za-z0-9._-]+/) || [])[0] || null;

const report = {
  ok: true,
  url: page.url(),
  title: await page.title(),
  hasCloudless: /Cloudless/i.test(text),
  productionNotApproved: /Not approved|not approved/i.test(text),
  hasSandbox: /Sandbox/i.test(text),
  hasDirectPost: /Direct(?:ly)? post|Direct Post/i.test(text),
  hasVerifyDomains: /Verify domains|URL propert/i.test(text),
  hasWrongJpDomain: /cloudless\.jp/i.test(text),
  expectedGrDomain: /cloudless\.gr/i.test(text),
  domainTokenPresent: Boolean(domainToken),
  domainToken: domainToken,
  redirectLooksWrong: /social\.cloudless\.jp/i.test(text),
  redirectLooksCorrect: /social\.cloudless\.gr\/api\/v1\/auth\/oauth\/tiktok\/callback/i.test(text),
  submitReviewVisible: /Submit for review|submit for audit/i.test(text),
  snippet: text.slice(0, 12000),
};
write('inspect-app.json', report);
console.log(JSON.stringify({
  ok: true,
  url: report.url,
  productionNotApproved: report.productionNotApproved,
  hasWrongJpDomain: report.hasWrongJpDomain,
  redirectLooksCorrect: report.redirectLooksCorrect,
  domainTokenPresent: report.domainTokenPresent,
  submitReviewVisible: report.submitReviewVisible,
}));
await browser.close();
