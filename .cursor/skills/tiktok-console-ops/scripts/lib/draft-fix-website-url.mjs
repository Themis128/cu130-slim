/**
 * Return Cloudless app to draft, set Website URL to https://cloudless.gr, save.
 * Does NOT submit for review unless SUBMIT_REVIEW=1.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = process.env.TIKTOK_APP_ID || '7630494700880906241';
const WEB_URL = 'https://cloudless.gr';
const SUBMIT = process.env.SUBMIT_REVIEW === '1';

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismissCookies(page) {
  await page.evaluate(() => {
    const t = [...document.querySelectorAll('button')].find((b) => /allow all/i.test(b.textContent || ''));
    if (t) t.click();
  }).catch(() => {});
  await page.waitForTimeout(700);
}

async function listUrlInputs(page) {
  const inputs = page.locator('input[type="text"], input[type="url"], input:not([type]), textarea');
  const n = await inputs.count();
  const out = [];
  for (let i = 0; i < n; i++) {
    const el = inputs.nth(i);
    if (!(await el.isVisible().catch(() => false))) continue;
    out.push({
      i,
      disabled: await el.isDisabled().catch(() => true),
      value: ((await el.inputValue().catch(() => '')) || '').trim(),
    });
  }
  return out;
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
const report = { steps: [] };

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
  write('draft-fix-web.json', { ok: false, error: 'login_failed' });
  process.exit(2);
}
report.steps.push('logged_in');

await page.goto(`https://developers.tiktok.com/app/${APP_ID}/pending`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(4500);
await dismissCookies(page);
await shot(page, 'draft-01-pending.png');
report.before = await listUrlInputs(page);

// Return to Draft
const returnDraft = page.getByRole('button', { name: /Return to Draft/i }).first();
const returnText = page.getByText(/Return to Draft/i).first();
let returned = false;
if (await returnDraft.isVisible().catch(() => false)) {
  await returnDraft.click({ force: true });
  returned = true;
} else if (await returnText.isVisible().catch(() => false)) {
  await returnText.click({ force: true });
  returned = true;
} else {
  // try evaluate click
  returned = await page.evaluate(() => {
    const el = [...document.querySelectorAll('button, a, [role="button"]')]
      .find((e) => /Return to Draft/i.test(e.textContent || ''));
    if (el) { el.click(); return true; }
    return false;
  });
}
report.steps.push(returned ? 'clicked_return_to_draft' : 'return_to_draft_not_found');
await page.waitForTimeout(4000);
await dismissCookies(page);

// Confirm dialog if any
for (const name of [/confirm/i, /yes/i, /continue/i, /ok/i, /return/i]) {
  const b = page.getByRole('button', { name }).first();
  if (await b.isVisible().catch(() => false) && await b.isEnabled().catch(() => false)) {
    const label = await b.innerText().catch(() => '');
    if (/cancel|close/i.test(label)) continue;
    await b.click({ force: true }).catch(() => {});
    await page.waitForTimeout(2000);
    report.steps.push(`confirm:${label.slice(0, 40)}`);
    break;
  }
}
await dismissCookies(page);
await shot(page, 'draft-02-after-return.png');

// Navigate to editable app page if needed
if (/\/pending/.test(page.url()) && (await listUrlInputs(page)).every((x) => x.disabled || !x.value.includes('social.cloudless.gr'))) {
  await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  });
  await page.waitForTimeout(4000);
  await dismissCookies(page);
}
await shot(page, 'draft-03-edit.png');
report.mid = await listUrlInputs(page);

// Fill website URL
let updated = 0;
const changes = [];
const inputs = page.locator('input[type="text"], input[type="url"], input:not([type])');
const n = await inputs.count();
for (let i = 0; i < n; i++) {
  const el = inputs.nth(i);
  if (!(await el.isVisible().catch(() => false))) continue;
  if (await el.isDisabled().catch(() => true)) continue;
  const cur = ((await el.inputValue().catch(() => '')) || '').trim();
  if (/^https?:\/\/(www\.)?social\.cloudless\.gr\/?$/i.test(cur)
    || /^https?:\/\/(www\.)?(social\.)?cloudless\.(jp|app)\/?$/i.test(cur)) {
    await el.click({ force: true });
    await el.fill('');
    await el.fill(WEB_URL);
    updated += 1;
    changes.push({ from: cur, to: WEB_URL });
  }
}
report.updated = updated;
report.changes = changes;
report.steps.push(updated ? `filled_${updated}` : 'no_editable_website_field');

// Save
let saved = false;
for (const name of [/save changes/i, /^save$/i, /apply/i, /update/i]) {
  const b = page.getByRole('button', { name }).first();
  if (await b.isVisible().catch(() => false) && await b.isEnabled().catch(() => false)) {
    await b.click({ force: true });
    saved = true;
    await page.waitForTimeout(3500);
    report.steps.push(`saved:${name}`);
    break;
  }
}
report.saved = saved;
await dismissCookies(page);
await shot(page, 'draft-04-after-save.png');
report.after = await listUrlInputs(page);

const hasWeb = report.after.some((v) => /^https?:\/\/(www\.)?cloudless\.gr\/?$/i.test(v.value));
const stillSocialWeb = report.after.some((v) => /^https?:\/\/(www\.)?social\.cloudless\.gr\/?$/i.test(v.value) && !v.disabled);

if (SUBMIT) {
  const sub = page.getByRole('button', { name: /Submit for review|Submit/i }).first();
  if (await sub.isVisible().catch(() => false) && await sub.isEnabled().catch(() => false)) {
    await sub.click({ force: true });
    await page.waitForTimeout(3000);
    report.steps.push('submitted_for_review');
  }
}

write('draft-fix-web.json', {
  ok: true,
  url: page.url(),
  updated,
  saved,
  hasWebUrlCloudlessGr: hasWeb,
  stillSocialWebEditable: stillSocialWeb,
  changes,
  steps: report.steps,
  before: report.before,
  mid: report.mid,
  after: report.after.filter((v) => v.value),
});
console.log(JSON.stringify({
  ok: true,
  url: page.url(),
  updated,
  saved,
  hasWebUrlCloudlessGr: hasWeb,
  changes,
  steps: report.steps,
}, null, 2));
await browser.close();
