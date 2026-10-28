/**
 * Open URL properties modal on /pending and extract domain verification TXT.
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

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismissCookies(page) {
  await page.getByRole('button', { name: /Allow all/i }).click({ timeout: 2500 }).catch(() => {});
  await page.waitForTimeout(400);
}

function extractToken(text) {
  const m = text.match(/tiktok-domain-verification=[A-Za-z0-9._-]+/);
  return m ? m[0] : null;
}

const browser = await chromium.launch({ headless: true, args: ['--disable-blink-features=AutomationControlled'] });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
  storageState: fs.existsSync(STATE) ? STATE : undefined,
});
const page = await context.newPage();

async function loginIfNeeded() {
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

await loginIfNeeded();
await page.goto(`https://developers.tiktok.com/app/${APP_ID}/pending`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(5000);
await dismissCookies(page);

// Click URL properties button if present, else Verify under Content Posting
const urlProps = page.getByRole('button', { name: /URL properties/i }).or(page.getByText(/^URL properties$/i));
if (await urlProps.count()) {
  await urlProps.first().click({ force: true });
} else {
  await page.getByRole('button', { name: /^Verify$/i }).last().click({ force: true });
}
await page.waitForTimeout(3000);
await dismissCookies(page);
await shot(page, 'u01-modal.png');

// Click cloudless.gr row / domain entry
await page.getByText('cloudless.gr', { exact: false }).first().click({ force: true }).catch(() => {});
await page.waitForTimeout(2000);
await shot(page, 'u02-domain-row.png');

// Click Verify properties
const vp = page.getByRole('button', { name: /Verify properties|Verify domain|Verify/i });
if (await vp.count()) {
  await vp.first().click({ force: true }).catch(() => {});
  await page.waitForTimeout(3000);
}
await shot(page, 'u03-after-verify-properties.png');

let text = await page.locator('body').innerText();
let html = await page.content();
let token = extractToken(text) || extractToken(html);

// Expand any "DNS" / "TXT" instructions
for (const label of [/DNS/i, /TXT/i, /Add a TXT/i, /verification record/i, /Show/i, /Copy/i]) {
  const el = page.getByText(label).first();
  if (await el.count()) await el.click({ force: true }).catch(() => {});
}
await page.waitForTimeout(1500);
text = await page.locator('body').innerText();
html = await page.content();
token = token || extractToken(text) || extractToken(html);

// Sometimes token is in a code/pre or copy field without the prefix
if (!token) {
  const m2 = (text + '\n' + html).match(/tiktok-domain-verification[=:][\s]*([A-Za-z0-9._-]+)/);
  if (m2) token = `tiktok-domain-verification=${m2[1]}`;
}
if (!token) {
  // bare long token near verification wording
  const m3 = text.match(/verification[^\n]{0,40}\n([A-Za-z0-9._-]{20,})/i);
  if (m3) token = `tiktok-domain-verification=${m3[1]}`;
}

write('url-properties-text.json', { snippet: text.slice(0, 10000), hasToken: Boolean(token) });
if (token) {
  write('domain-verification-token.json', { token, domain: 'cloudless.gr' });
  console.log('FOUND_DOMAIN_TOKEN');
} else {
  write('domain-verification-token.json', { token: null, domain: 'cloudless.gr' });
  console.log('NO_DOMAIN_TOKEN');
}

if (CLICK_VERIFY) {
  await page.getByRole('button', { name: /Verify properties|^Verify$/i }).first().click({ force: true }).catch(() => {});
  await page.waitForTimeout(8000);
  await shot(page, 'u04-final-verify.png');
  write('url-properties-after-verify.json', { snippet: (await page.locator('body').innerText()).slice(0, 6000) });
  console.log('CLICKED_VERIFY');
}

await context.storageState({ path: STATE });
console.log(JSON.stringify({ ok: true, hasToken: Boolean(token), url: page.url() }));
await browser.close();
