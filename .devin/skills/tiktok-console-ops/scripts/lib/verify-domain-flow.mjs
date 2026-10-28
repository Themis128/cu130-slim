/**
 * Complete URL-property domain verification for cloudless.gr.
 * Handles: URL properties modal → Select property type (Domain) → DNS TXT → optional Verify.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = process.env.TIKTOK_APP_ID || '7630494700880906241';
const STATE = path.join(OUT, 'dev-console-state.json');
const CLICK_VERIFY = process.env.CLICK_VERIFY === '1';
const DOMAIN = 'cloudless.gr';

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismissCookies(page) {
  await page.getByRole('button', { name: /Allow all/i }).click({ timeout: 2000 }).catch(() => {});
  await page.waitForTimeout(300);
}

function extractToken(s) {
  const m = s.match(/tiktok-domain-verification=[A-Za-z0-9._-]+/);
  if (m) return m[0];
  const m2 = s.match(/tiktok-domain-verification[=:\s]+([A-Za-z0-9._-]{16,})/i);
  if (m2) return `tiktok-domain-verification=${m2[1]}`;
  return null;
}

async function loginIfNeeded(page, context) {
  await page.goto('https://developers.tiktok.com/apps/', { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForTimeout(2000);
  await dismissCookies(page);
  const t = await page.locator('body').innerText();
  if (!/Cloudless|Manage apps/i.test(t) || /No access|You need to login/i.test(t)) {
    await page.goto('https://developers.tiktok.com/login/', { waitUntil: 'domcontentloaded', timeout: 120000 });
    await dismissCookies(page);
    await page.getByPlaceholder('Email').fill(EMAIL);
    await page.getByPlaceholder('Password').fill(PASSWORD);
    const btn = page.getByRole('button', { name: /^Log in$/i });
    for (let i = 0; i < 30; i++) {
      if (!(await btn.isDisabled().catch(() => true))) break;
      await page.waitForTimeout(200);
    }
    await btn.click({ force: true });
    await page.waitForTimeout(8000);
    await dismissCookies(page);
    await context.storageState({ path: STATE });
  }
}

const browser = await chromium.launch({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled'],
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
  storageState: fs.existsSync(STATE) ? STATE : undefined,
});
const page = await context.newPage();

await loginIfNeeded(page, context);
await page.goto(`https://developers.tiktok.com/app/${APP_ID}/pending`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(5000);
await dismissCookies(page);
await shot(page, 'v01-pending.png');

// Open URL properties — prefer explicit button, else Content Posting Verify
const openers = [
  page.getByRole('button', { name: /URL properties/i }),
  page.getByText(/^URL properties$/i),
  page.getByRole('button', { name: /^Verify$/i }),
];
for (const loc of openers) {
  if (await loc.count()) {
    await loc.last().click({ force: true }).catch(() => {});
    await page.waitForTimeout(2500);
    await dismissCookies(page);
    break;
  }
}
await shot(page, 'v02-opened.png');

let text = await page.locator('body').innerText();

// If "Select property type" is visible, choose Domain and continue
if (/Select property type/i.test(text)) {
  // Click the Domain card (not URL prefix)
  const domainCard = page.getByText(/^Domain$/).first();
  await domainCard.click({ force: true });
  await page.waitForTimeout(800);
  // Next / Continue / Add
  const next = page.getByRole('button', { name: /next|continue|add|confirm|create/i });
  if (await next.count()) {
    const b = next.first();
    if (await b.isEnabled().catch(() => false)) await b.click({ force: true });
  }
  await page.waitForTimeout(2000);
  await shot(page, 'v03-domain-selected.png');
}

text = await page.locator('body').innerText();

// Fill domain input if prompted
const inputs = page.locator('input:visible');
const ic = await inputs.count();
for (let i = 0; i < ic; i++) {
  const el = inputs.nth(i);
  if (await el.isDisabled().catch(() => true)) continue;
  const ph = ((await el.getAttribute('placeholder')) || '').toLowerCase();
  const val = (await el.inputValue().catch(() => '')) || '';
  if (/domain|example\.com|your domain|http/i.test(ph) || val === '' || /example/i.test(val)) {
    // only fill if this looks like domain entry (not search boxes with long unrelated text)
    if (ph.includes('search')) continue;
    await el.fill(DOMAIN).catch(() => {});
    await page.waitForTimeout(500);
    break;
  }
}
const addConfirm = page.getByRole('button', { name: /add|confirm|create|save|next|continue|submit/i });
if (await addConfirm.count()) {
  const b = addConfirm.first();
  if (await b.isEnabled().catch(() => false)) {
    await b.click({ force: true });
    await page.waitForTimeout(3000);
  }
}
await shot(page, 'v04-after-domain-entry.png');

// Click unverified cloudless.gr row if listed
await page.getByText(DOMAIN, { exact: true }).first().click({ force: true }).catch(() => {});
await page.waitForTimeout(2000);
await shot(page, 'v05-domain-detail.png');

// Expand DNS / copy sections
for (const label of [
  /DNS record/i,
  /TXT/i,
  /Copy/i,
  /Show record/i,
  /verification record/i,
  /Get record/i,
]) {
  const el = page.getByText(label).first();
  if (await el.count()) await el.click({ force: true }).catch(() => {});
}
await page.waitForTimeout(1000);

text = await page.locator('body').innerText();
let html = await page.content();
let token = extractToken(text) || extractToken(html);

// Copy buttons often put value on clipboard — try adjacent code/pre text
if (!token) {
  const codeish = await page.locator('code, pre, [class*="copy"], [class*=" Cop"], input:visible').allTextContents().catch(() => []);
  const joined = codeish.join('\n');
  token = extractToken(joined);
  if (!token) {
    // input values
    for (let i = 0; i < ic; i++) {
      const el = inputs.nth(i);
      const val = (await el.inputValue().catch(() => '')) || '';
      const t = extractToken(val);
      if (t) {
        token = t;
        break;
      }
      if (/^[A-Za-z0-9._-]{20,}$/.test(val) && !val.includes('http')) {
        token = `tiktok-domain-verification=${val}`;
        break;
      }
    }
  }
}

write('verify-flow-text.json', { snippet: text.slice(0, 12000), hasToken: Boolean(token) });
if (token) {
  write('domain-verification-token.json', { token, domain: DOMAIN });
  console.log('FOUND_DOMAIN_TOKEN');
} else {
  write('domain-verification-token.json', { token: null, domain: DOMAIN });
  console.log('NO_DOMAIN_TOKEN');
}

if (CLICK_VERIFY) {
  const vp = page.getByRole('button', { name: /Verify properties|^Verify$/i });
  if (await vp.count()) {
    await vp.first().click({ force: true });
    await page.waitForTimeout(8000);
    await shot(page, 'v06-verified.png');
    write('verify-result.json', { snippet: (await page.locator('body').innerText()).slice(0, 5000) });
    console.log('CLICKED_VERIFY');
  }
}

await context.storageState({ path: STATE });
console.log(JSON.stringify({ ok: true, hasToken: Boolean(token), url: page.url() }));
await browser.close();
