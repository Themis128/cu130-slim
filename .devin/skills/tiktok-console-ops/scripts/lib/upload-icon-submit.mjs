import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const ICON = process.env.ICON_PATH || '/assets/app-icon-1024.png';
const APP_ID = '7630494700880906241';
const WEB = 'https://cloudless.gr';
const TOS = 'https://cloudless.gr/en/terms';
const PRIVACY = 'https://cloudless.gr/en/privacy';

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismiss(page) {
  for (let i = 0; i < 5; i++) {
    await page.evaluate(() => {
      [...document.querySelectorAll('button')].find((b) => /allow all/i.test(b.textContent || ''))?.click();
    }).catch(() => {});
    await page.waitForTimeout(500);
  }
}

async function setIf(page, re, to) {
  let n = 0;
  for (const el of await page.locator('input').all()) {
    if (!(await el.isVisible().catch(() => false))) continue;
    if (await el.isDisabled().catch(() => true)) continue;
    const cur = ((await el.inputValue().catch(() => '')) || '').trim();
    if (re.test(cur) && cur !== to) { await el.fill(to); n++; }
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
  write('icon-submit.json', { ok: false, error: 'login_failed' });
  process.exit(2);
}
steps.push('login');

await page.goto(`https://developers.tiktok.com/app/${APP_ID}/pending`, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(4500);
await dismiss(page);
if (/Return to Draft/i.test(await page.locator('body').innerText())) {
  await page.evaluate(() => [...document.querySelectorAll('button,a')].find((e) => /Return to Draft/i.test(e.textContent || ''))?.click());
  await page.waitForTimeout(1200);
  await page.evaluate(() => [...document.querySelectorAll('button')].find((e) => /^Confirm$/i.test((e.textContent || '').trim()))?.click());
  await page.waitForTimeout(4500);
  steps.push('return_draft');
}
await dismiss(page);

// Ensure URLs
await setIf(page, /^https?:\/\/(www\.)?social\.cloudless\.gr\/?$/i, WEB);
await setIf(page, /^https?:\/\/(www\.)?cloudless\.gr\/terms\/?$/i, TOS);
await setIf(page, /^https?:\/\/(www\.)?cloudless\.gr\/privacy\/?$/i, PRIVACY);
if (!(await page.locator('input').evaluateAll((els) => els.some((e) => /^https?:\/\/(www\.)?cloudless\.gr\/?$/i.test((e.value || '').trim()))))) {
  // set any empty-looking web field near Configure — fallback: fill matching social again already done
}
await setIf(page, /^https?:\/\/social\.cloudless\.gr\/?$/i, WEB);

// Upload icon via file input
const fileInputs = page.locator('input[type="file"]');
const fc = await fileInputs.count();
steps.push(`file_inputs:${fc}`);
let uploaded = false;
if (fc > 0) {
  // Prefer first image accept
  for (let i = 0; i < fc; i++) {
    const inp = fileInputs.nth(i);
    const accept = ((await inp.getAttribute('accept')) || '').toLowerCase();
    if (!accept || /image|png|jpg|jpeg/.test(accept)) {
      await inp.setInputFiles(ICON);
      uploaded = true;
      steps.push(`uploaded_via_input_${i}`);
      await page.waitForTimeout(3000);
      break;
    }
  }
}
if (!uploaded) {
  // Click upload area near App icon
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser', { timeout: 8000 }).catch(() => [null]),
    page.evaluate(() => {
      const el = [...document.querySelectorAll('*')].find((e) => /App icon/i.test(e.textContent || '') && (e.textContent || '').length < 40);
      const root = el?.closest('section,div') || el?.parentElement;
      const btn = root && [...root.querySelectorAll('button, [role="button"], label')].find((b) => /upload|change|replace|browse|add/i.test(b.textContent || '') || true);
      (btn || el)?.click();
    }),
  ]);
  const ch = Array.isArray(chooser) ? chooser[0] : chooser;
  if (ch) {
    await ch.setFiles(ICON);
    uploaded = true;
    steps.push('uploaded_via_chooser');
    await page.waitForTimeout(3000);
  }
}
await dismiss(page);
await shot(page, 'icon-01-uploaded.png');

await page.evaluate(() => {
  const b = [...document.querySelectorAll('button')].find((e) => /^Save$/i.test((e.textContent || '').trim()));
  if (b && !b.disabled) b.click();
});
await page.waitForTimeout(3500);
await dismiss(page);

const text = await page.locator('body').innerText();
const formError = /This form has \d+ error/i.test(text);
const iconRequired = /App icon is required/i.test(text);
steps.push(formError ? 'form_error' : 'form_clean');
steps.push(iconRequired ? 'icon_still_required' : 'icon_ok');
await shot(page, 'icon-02-after-save.png');

const fields = [];
for (const el of await page.locator('input').all()) {
  if (!(await el.isVisible().catch(() => false))) continue;
  const v = ((await el.inputValue().catch(() => '')) || '').trim();
  if (/https?:\/\//.test(v)) fields.push(v);
}
const websiteOk = fields.some((v) => /^https?:\/\/(www\.)?cloudless\.gr\/?$/i.test(v));

let submitted = false;
if (!formError && !iconRequired && websiteOk) {
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
await shot(page, 'icon-03-final.png');
const finalText = await page.locator('body').innerText();
const out = {
  ok: true,
  uploaded,
  formError,
  iconRequired,
  websiteOk,
  submitted,
  steps,
  fields,
  url: page.url(),
  notApproved: /Not approved/i.test(finalText),
  underReview: /Under review/i.test(finalText) && !/to Rejected/i.test(finalText),
};
write('icon-submit.json', out);
console.log(JSON.stringify(out, null, 2));
await browser.close();
