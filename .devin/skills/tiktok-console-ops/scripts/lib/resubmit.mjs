/**
 * Return to draft (if needed), ensure Website URL is https://cloudless.gr,
 * then submit for production review. Does not navigate back to /pending snapshot.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = process.env.TIKTOK_APP_ID || '7630494700880906241';
const WEB = 'https://cloudless.gr';
const SUBMIT = process.env.SUBMIT_REVIEW === '1';

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function nuke(page) {
  await page.evaluate(() => {
    const t = [...document.querySelectorAll('button')].find((b) => /allow all/i.test(b.textContent || ''));
    t?.click();
    for (const el of document.querySelectorAll('[class*="cookie"], [id*="cookie"]')) {
      if (/cookie|consent|allow all/i.test(el.textContent || '')) el.remove();
    }
  }).catch(() => {});
  await page.waitForTimeout(500);
}

async function listHttp(page) {
  const out = [];
  for (const el of await page.locator('input').all()) {
    if (!(await el.isVisible().catch(() => false))) continue;
    const v = ((await el.inputValue().catch(() => '')) || '').trim();
    if (/https?:\/\//i.test(v) || v === 'Cloudless') {
      out.push({ disabled: await el.isDisabled().catch(() => true), value: v.slice(0, 120) });
    }
  }
  return out;
}

async function editableWebsite(page) {
  for (const el of await page.locator('input').all()) {
    if (!(await el.isVisible().catch(() => false))) continue;
    if (await el.isDisabled().catch(() => true)) continue;
    const v = ((await el.inputValue().catch(() => '')) || '').trim();
    if (/^https?:\/\/(www\.)?(social\.)?cloudless\.(gr|jp|app)\/?$/i.test(v)) return el;
  }
  return null;
}

if (!EMAIL || !PASSWORD) process.exit(1);
const browser = await chromium.launch({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled'],
});
const page = await (await browser.newContext({ viewport: { width: 1440, height: 1100 } })).newPage();
const report = { steps: [] };

await page.goto('https://developers.tiktok.com/login/', { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(1000);
await nuke(page);
await page.getByPlaceholder('Email').fill(EMAIL);
await page.getByPlaceholder('Password').fill(PASSWORD);
await nuke(page);
const loginBtn = page.getByRole('button', { name: /^Log in$/i });
for (let i = 0; i < 25; i++) {
  if (!(await loginBtn.isDisabled().catch(() => true))) break;
  await page.waitForTimeout(200);
}
await loginBtn.click({ force: true });
await page.waitForTimeout(10000);
await nuke(page);
if (page.url().includes('/login')) {
  write('resubmit.json', { ok: false, error: 'login_failed' });
  process.exit(2);
}
report.steps.push('login');

await page.goto(`https://developers.tiktok.com/app/${APP_ID}/pending`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(4000);
await nuke(page);
report.before = await listHttp(page);
await shot(page, 'rs-01-pending.png');

// Always return to draft from rejected snapshot
const hasReturn = /Return to Draft/i.test(await page.locator('body').innerText());
if (hasReturn) {
  await page.evaluate(() => {
    const el = [...document.querySelectorAll('button, a')]
      .find((e) => /Return to Draft/i.test(e.textContent || ''));
    el?.click();
  });
  await page.waitForTimeout(1500);
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')]
      .find((e) => /^Confirm$/i.test((e.textContent || '').trim()));
    b?.click();
  });
  await page.waitForTimeout(4000);
  report.steps.push('returned_to_draft');
}
await nuke(page);

// Wait until website field is editable (up to ~20s)
let webEl = null;
for (let i = 0; i < 20; i++) {
  webEl = await editableWebsite(page);
  if (webEl) break;
  // Try app root if still locked
  if (i === 5) {
    await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, {
      waitUntil: 'domcontentloaded',
      timeout: 120000,
    });
    await page.waitForTimeout(3500);
    await nuke(page);
  }
  await page.waitForTimeout(1000);
}
await shot(page, 'rs-02-editable.png');
report.mid = await listHttp(page);

if (webEl) {
  const cur = ((await webEl.inputValue().catch(() => '')) || '').trim();
  if (!/^https?:\/\/(www\.)?cloudless\.gr\/?$/i.test(cur)) {
    await webEl.fill(WEB);
    report.steps.push(`set_web:${cur}->${WEB}`);
  } else {
    report.steps.push('web_already_cloudless_gr');
  }
} else {
  report.steps.push('website_field_still_locked');
}

// Soft-update apply reason if it still emphasizes social.cloudless.gr as the website
const reasonBox = page.locator('textarea').first();
if (await reasonBox.isVisible().catch(() => false) && !(await reasonBox.isDisabled().catch(() => true))) {
  let reason = (await reasonBox.inputValue().catch(() => '')) || '';
  if (reason && !/public marketing website is https:\/\/cloudless\.gr/i.test(reason)) {
    const note = ' Public marketing website is https://cloudless.gr (fully developed site with Privacy Policy and Terms of Service links visible without login). The product app at social.cloudless.gr is where Login Kit + Content Posting are integrated; demo video shows that flow.';
    if (reason.length + note.length < 1000) {
      await reasonBox.fill(reason.trimEnd() + note);
      report.steps.push('reason_appended');
    } else {
      // replace leading to fit note
      const trimmed = reason.slice(0, 1000 - note.length - 1);
      await reasonBox.fill(trimmed + note);
      report.steps.push('reason_rewritten_fit');
    }
  }
}

// Save if available
const saved = await page.evaluate(() => {
  const b = [...document.querySelectorAll('button')]
    .find((e) => /^(Save|Save changes)$/i.test((e.textContent || '').trim()));
  if (b && !b.disabled) { b.click(); return true; }
  return false;
});
report.saved = saved;
if (saved) await page.waitForTimeout(3000);
await nuke(page);
report.afterEdit = await listHttp(page);
await shot(page, 'rs-03-saved.png');

const websiteOk = report.afterEdit.some((i) => /^https?:\/\/(www\.)?cloudless\.gr\/?$/i.test(i.value) && !i.disabled)
  || report.afterEdit.some((i) => /^https?:\/\/(www\.)?cloudless\.gr\/?$/i.test(i.value));

let submitted = false;
if (SUBMIT && websiteOk) {
  const clicked = await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')]
      .find((e) => /Submit for review/i.test(e.textContent || '') && !e.disabled);
    if (b) { b.click(); return true; }
    return false;
  });
  report.steps.push(clicked ? 'clicked_submit' : 'no_submit_button');
  if (clicked) {
    await page.waitForTimeout(2000);
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('button')]
        .find((e) => /confirm|submit|yes|continue/i.test(e.textContent || '')
          && !/cancel|close|back|return/i.test(e.textContent || '') && !e.disabled);
      b?.click();
    });
    await page.waitForTimeout(5000);
    submitted = true;
  }
  await shot(page, 'rs-04-submit.png');
} else if (SUBMIT && !websiteOk) {
  report.steps.push('skip_submit_website_not_ok');
}

const text = await page.locator('body').innerText();
report.ok = true;
report.websiteOk = websiteOk;
report.submitted = submitted;
report.hasSubmitButton = /Submit for review/i.test(text);
report.underReview = /Under review/i.test(text) && !/Under review to Rejected/i.test(text);
report.notApproved = /Not approved/i.test(text);
report.url = page.url();
write('resubmit.json', report);
console.log(JSON.stringify({
  ok: true,
  websiteOk,
  saved,
  submitted,
  hasSubmitButton: report.hasSubmitButton,
  underReview: report.underReview,
  notApproved: report.notApproved,
  steps: report.steps,
  httpFields: report.afterEdit,
  url: report.url,
}, null, 2));
await browser.close();
