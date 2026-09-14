/**
 * Submit Cloudless TikTok app for production review after Website URL fix.
 * Env: SUBMIT_REVIEW=1 required (safety). DRY_RUN=1 only reports readiness.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = process.env.TIKTOK_APP_ID || '7630494700880906241';
const SUBMIT = process.env.SUBMIT_REVIEW === '1';
const DRY = process.env.DRY_RUN === '1' || !SUBMIT;

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismissCookies(page) {
  await page.evaluate(() => {
    const t = [...document.querySelectorAll('button')].find((b) => /allow all/i.test(b.textContent || ''));
    if (t) t.click();
  }).catch(() => {});
  await page.waitForTimeout(500);
}

if (!EMAIL || !PASSWORD) {
  console.error('Missing credentials');
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
  write('submit-review.json', { ok: false, error: 'login_failed' });
  process.exit(2);
}

await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(4500);
await dismissCookies(page);
await shot(page, 'submit-01.png');

const inputs = [];
const loc = page.locator('input[type="text"], input[type="url"], input:not([type]), textarea');
for (let i = 0; i < await loc.count(); i++) {
  const el = loc.nth(i);
  if (!(await el.isVisible().catch(() => false))) continue;
  inputs.push({
    disabled: await el.isDisabled().catch(() => true),
    value: ((await el.inputValue().catch(() => '')) || '').trim().slice(0, 200),
  });
}

const text = await page.locator('body').innerText();
const readiness = {
  websiteIsCloudlessGr: inputs.some((i) => /^https?:\/\/(www\.)?cloudless\.gr\/?$/i.test(i.value)),
  websiteStillSocial: inputs.some((i) => /^https?:\/\/(www\.)?social\.cloudless\.gr\/?$/i.test(i.value)),
  redirectOk: inputs.some((i) => i.value.includes('social.cloudless.gr/api/v1/auth/oauth/tiktok/callback'))
    || /social\.cloudless\.gr\/api\/v1\/auth\/oauth\/tiktok\/callback/.test(text),
  tosOk: /cloudless\.gr\/(en\/)?terms/i.test(text) || inputs.some((i) => /\/terms/.test(i.value)),
  privacyOk: /cloudless\.gr\/(en\/)?privacy/i.test(text) || inputs.some((i) => /\/privacy/.test(i.value)),
  hasDemo: /tiktok-demo\.mp4|\.mp4/i.test(text),
  submitVisible: /Submit for review/i.test(text),
  underReview: /Under review/i.test(text),
  notApproved: /Not approved/i.test(text),
};

const submitBtn = page.getByRole('button', { name: /Submit for review/i }).first();
const canClick = await submitBtn.isVisible().catch(() => false)
  && await submitBtn.isEnabled().catch(() => false);

let submitted = false;
if (!DRY && canClick && readiness.websiteIsCloudlessGr && !readiness.websiteStillSocial) {
  await submitBtn.click({ force: true });
  await page.waitForTimeout(2500);
  // Confirm dialog
  for (const name of [/confirm/i, /submit/i, /yes/i, /continue/i]) {
    const b = page.getByRole('button', { name }).first();
    if (await b.isVisible().catch(() => false) && await b.isEnabled().catch(() => false)) {
      const label = (await b.innerText().catch(() => '')).toLowerCase();
      if (/cancel|close|back/.test(label)) continue;
      await b.click({ force: true }).catch(() => {});
      await page.waitForTimeout(3000);
      break;
    }
  }
  submitted = true;
  await shot(page, 'submit-02-after.png');
} else {
  await shot(page, 'submit-02-skipped.png');
}

const afterText = await page.locator('body').innerText();
const report = {
  ok: true,
  dryRun: DRY,
  submitted,
  canClick,
  readiness,
  url: page.url(),
  underReviewNow: /Under review/i.test(afterText),
  inputs: inputs.filter((i) => i.value).slice(0, 15),
};
write('submit-review.json', report);
console.log(JSON.stringify({
  ok: true,
  dryRun: DRY,
  submitted,
  canClick,
  readiness,
  underReviewNow: report.underReviewNow,
  url: report.url,
}, null, 2));
await browser.close();
