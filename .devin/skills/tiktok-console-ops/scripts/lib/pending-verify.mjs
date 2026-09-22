/**
 * Robust console session: login → save storage → open /pending → verify domains.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = process.env.TIKTOK_APP_ID || '7630494700880906241';
const CLICK_VERIFY = process.env.CLICK_VERIFY === '1';
const STATE = path.join(OUT, 'dev-console-state.json');

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismissCookies(page) {
  await page.getByRole('button', { name: /Allow all/i }).click({ timeout: 3000 }).catch(() => {});
  await page.evaluate(() => {
    for (const b of document.querySelectorAll('button')) {
      if (/allow all/i.test(b.textContent || '')) b.click();
    }
  }).catch(() => {});
  await page.waitForTimeout(500);
}

function extractToken(text) {
  const m = text.match(/tiktok-domain-verification=[A-Za-z0-9._-]+/);
  return m ? m[0] : null;
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

async function ensureLogin() {
  await page.goto('https://developers.tiktok.com/apps/', { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForTimeout(2500);
  await dismissCookies(page);
  const body = await page.locator('body').innerText();
  if (/No access|You need to login|Log in with your TikTok developer/i.test(body) || page.url().includes('/login')) {
    await page.goto('https://developers.tiktok.com/login/', { waitUntil: 'domcontentloaded', timeout: 120000 });
    await page.waitForTimeout(1500);
    await dismissCookies(page);
    await page.getByPlaceholder('Email').fill(EMAIL);
    await page.getByPlaceholder('Password').fill(PASSWORD);
    await page.waitForTimeout(500);
    await dismissCookies(page);
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

await ensureLogin();
await shot(page, 'p01-after-login.png');

// Always use pending config surface (production draft)
const pendingUrl = `https://developers.tiktok.com/app/${APP_ID}/pending`;
await page.goto(pendingUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(5000);
await dismissCookies(page);
await shot(page, 'p02-pending.png');

let text = await page.locator('body').innerText();
if (/No access|You need to login/i.test(text)) {
  // re-login once
  await ensureLogin();
  await page.goto(pendingUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForTimeout(5000);
  await dismissCookies(page);
  await shot(page, 'p02b-pending-retry.png');
  text = await page.locator('body').innerText();
}

write('pending-snippet.json', {
  url: page.url(),
  hasCloudless: /Cloudless/i.test(text),
  hasVerify: /Verify/i.test(text),
  hasContentPosting: /Content Posting/i.test(text),
  hasRedirectGr: /social\.cloudless\.gr\/api\/v1\/auth\/oauth\/tiktok\/callback/.test(text),
  hasJp: /cloudless\.jp/i.test(text),
  hasApp: /cloudless\.app/i.test(text),
  notApproved: /Not approved/i.test(text),
  snippet: text.slice(0, 12000),
});

// Collect all input values (non-secret)
const inputs = await page.evaluate(() =>
  [...document.querySelectorAll('input, textarea')].map((el) => ({
    type: el.getAttribute('type'),
    disabled: el.disabled,
    value: (el.value || '').slice(0, 200),
    placeholder: el.getAttribute('placeholder') || '',
  })),
);
write('pending-inputs.json', inputs.filter((i) => i.value || i.placeholder));

// Click Verify near Content Posting
await page.evaluate(() => {
  const nodes = [...document.querySelectorAll('button, a, div[role="button"]')];
  const v = nodes.find((n) => /^\s*Verify\s*$/i.test((n.textContent || '').trim()));
  if (v) v.scrollIntoView({ block: 'center' });
});
await page.waitForTimeout(800);
await dismissCookies(page);

const verifyButtons = page.locator('button', { hasText: /^Verify$/i });
const vc = await verifyButtons.count();
write('verify-button-count.json', { count: vc });
if (vc > 0) {
  // Prefer the one near "Verify domains" / Content Posting
  await verifyButtons.last().click({ force: true }).catch(async () => {
    await verifyButtons.first().click({ force: true });
  });
  await page.waitForTimeout(4000);
  await dismissCookies(page);
  await shot(page, 'p03-after-verify-click.png');
}

text = await page.locator('body').innerText();
let token = extractToken(text) || extractToken(await page.content());

if (!token) {
  // Look for Add domain UI
  const add = page.getByRole('button', { name: /add domain|add url|add property|add/i });
  const ac = await add.count();
  for (let i = 0; i < ac; i++) {
    const b = add.nth(i);
    if ((await b.isVisible()) && (await b.isEnabled().catch(() => false))) {
      await b.click({ force: true });
      await page.waitForTimeout(1500);
      break;
    }
  }
  await page.getByText('Domain', { exact: true }).first().click().catch(() => {});
  const domainInputs = page.locator('input:visible');
  const di = await domainInputs.count();
  for (let i = 0; i < di; i++) {
    const el = domainInputs.nth(i);
    if (await el.isDisabled().catch(() => true)) continue;
    const val = await el.inputValue().catch(() => '');
    if (!val || /domain|example|http|cloudless/i.test(val + ((await el.getAttribute('placeholder')) || ''))) {
      await el.fill('cloudless.gr').catch(() => {});
      break;
    }
  }
  await page.getByRole('button', { name: /add|confirm|create|save|next|continue/i }).first().click({ force: true }).catch(() => {});
  await page.waitForTimeout(4000);
  await shot(page, 'p04-domain-modal.png');
  text = await page.locator('body').innerText();
  token = extractToken(text) || extractToken(await page.content());
}

if (token) {
  write('domain-verification-token.json', { token, domain: 'cloudless.gr' });
  console.log('FOUND_DOMAIN_TOKEN');
} else {
  write('domain-verification-token.json', { token: null, domain: 'cloudless.gr' });
  console.log('NO_DOMAIN_TOKEN');
}

if (CLICK_VERIFY && token) {
  await page.getByRole('button', { name: /^Verify$/i }).first().click({ force: true }).catch(() => {});
  await page.waitForTimeout(7000);
  await shot(page, 'p05-verified.png');
  console.log('CLICKED_VERIFY');
}

await context.storageState({ path: STATE });
write('pending-final.json', {
  url: page.url(),
  hasToken: Boolean(token),
  redirectOk: inputs.some((i) => (i.value || '').includes('social.cloudless.gr/api/v1/auth/oauth/tiktok/callback')),
});
console.log(JSON.stringify({
  ok: true,
  url: page.url(),
  hasToken: Boolean(token),
  redirectOk: inputs.some((i) => (i.value || '').includes('social.cloudless.gr/api/v1/auth/oauth/tiktok/callback')),
}));
await browser.close();
