import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = '7630494700880906241';
const WEB = 'https://cloudless.gr';
const TOS = 'https://cloudless.gr/en/terms';
const PRIVACY = 'https://cloudless.gr/en/privacy';

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismiss(page) {
  for (let i = 0; i < 4; i++) {
    await page.evaluate(() => {
      [...document.querySelectorAll('button')].find((b) => /allow all/i.test(b.textContent || ''))?.click();
    }).catch(() => {});
    await page.waitForTimeout(600);
  }
}

async function listHttp(page) {
  const out = [];
  for (const el of await page.locator('input').all()) {
    if (!(await el.isVisible().catch(() => false))) continue;
    const v = ((await el.inputValue().catch(() => '')) || '').trim();
    if (/https?:\/\//.test(v)) out.push({ disabled: await el.isDisabled().catch(() => true), value: v });
  }
  return out;
}

async function setExact(page, fromRe, to) {
  let n = 0;
  for (const el of await page.locator('input').all()) {
    if (!(await el.isVisible().catch(() => false))) continue;
    if (await el.isDisabled().catch(() => true)) continue;
    const cur = ((await el.inputValue().catch(() => '')) || '').trim();
    if (fromRe.test(cur) && cur !== to) {
      await el.fill(to);
      n += 1;
    }
  }
  return n;
}

const browser = await chromium.launch({ headless: true, args: ['--disable-blink-features=AutomationControlled'] });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 1100 } })).newPage();
const steps = [];

await page.goto('https://developers.tiktok.com/login/', { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(1500);
await dismiss(page);
await page.getByPlaceholder('Email').fill(EMAIL);
await page.getByPlaceholder('Password').fill(PASSWORD);
await page.waitForTimeout(400);
await dismiss(page);
const loginBtn = page.getByRole('button', { name: /^Log in$/i });
for (let i = 0; i < 40; i++) {
  if (!(await loginBtn.isDisabled().catch(() => true))) break;
  await page.waitForTimeout(250);
}
await loginBtn.click({ force: true });
await Promise.race([
  page.waitForURL((u) => !String(u).includes('/login'), { timeout: 60000 }).catch(() => {}),
  page.waitForTimeout(25000),
]);
await dismiss(page);
if (page.url().includes('/login')) {
  write('final-resubmit.json', { ok: false, error: 'login_failed' });
  console.log(JSON.stringify({ ok: false, error: 'login_failed' }));
  await browser.close();
  process.exit(2);
}
steps.push('login');

await page.goto(`https://developers.tiktok.com/app/${APP_ID}/pending`, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(4500);
await dismiss(page);

if (/Return to Draft/i.test(await page.locator('body').innerText())) {
  await page.evaluate(() => {
    [...document.querySelectorAll('button, a')].find((e) => /Return to Draft/i.test(e.textContent || ''))?.click();
  });
  await page.waitForTimeout(1500);
  await page.evaluate(() => {
    [...document.querySelectorAll('button')].find((e) => /^Confirm$/i.test((e.textContent || '').trim()))?.click();
  });
  await page.waitForTimeout(4500);
  steps.push('return_draft');
}
await dismiss(page);
await shot(page, 'final-01-draft.png');

const c1 = await setExact(page, /^https?:\/\/(www\.)?social\.cloudless\.gr\/?$/i, WEB);
const c2 = await setExact(page, /^https?:\/\/(www\.)?cloudless\.gr\/terms\/?$/i, TOS);
const c3 = await setExact(page, /^https?:\/\/(www\.)?cloudless\.gr\/privacy\/?$/i, PRIVACY);
// Also if already wrong non-en paths
const c4 = await setExact(page, /^https?:\/\/cloudless\.gr\/?$/i, WEB);
steps.push(`fills:${c1},${c2},${c3},${c4}`);

await page.evaluate(() => {
  const b = [...document.querySelectorAll('button')].find((e) => /^Save$/i.test((e.textContent || '').trim()));
  if (b && !b.disabled) b.click();
});
await page.waitForTimeout(3000);
await dismiss(page);

const fields = await listHttp(page);
const websiteOk = fields.some((f) => /^https?:\/\/(www\.)?cloudless\.gr\/?$/i.test(f.value));
const tosOk = fields.some((f) => /\/en\/terms/i.test(f.value));
const privacyOk = fields.some((f) => /\/en\/privacy/i.test(f.value));
steps.push(websiteOk ? 'website_ok' : 'website_bad');
steps.push(tosOk ? 'tos_ok' : 'tos_bad');
steps.push(privacyOk ? 'privacy_ok' : 'privacy_bad');
await shot(page, 'final-02-saved.png');

const body = await page.locator('body').innerText();
const formError = /This form has \d+ error/i.test(body);
steps.push(formError ? 'form_has_error' : 'form_clean');

let submitted = false;
if (websiteOk && !formError) {
  const clicked = await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find((e) => /Submit for review/i.test(e.textContent || '') && !e.disabled);
    if (b) { b.click(); return true; }
    return false;
  });
  steps.push(clicked ? 'submit_clicked' : 'submit_missing');
  if (clicked) {
    await page.waitForTimeout(2000);
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('button')].find((e) =>
        /confirm|submit|yes|continue/i.test(e.textContent || '')
        && !/cancel|close|back|return/i.test(e.textContent || '') && !e.disabled);
      b?.click();
    });
    await page.waitForTimeout(6000);
    submitted = true;
  }
} else {
  steps.push('skip_submit');
}
await shot(page, 'final-03.png');

const text = await page.locator('body').innerText();
const out = {
  ok: true,
  websiteOk,
  tosOk,
  privacyOk,
  formError,
  submitted,
  steps,
  fields,
  url: page.url(),
  notApproved: /Not approved/i.test(text),
  underReview: /Under review/i.test(text) && !/to Rejected/i.test(text),
};
write('final-resubmit.json', out);
console.log(JSON.stringify(out, null, 2));
await browser.close();
