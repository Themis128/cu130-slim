/**
 * Capture Content Posting domain verification TXT for cloudless.gr.
 * Safer than previous version: never fills disabled/app-name fields.
 * Env: CLICK_VERIFY=1 to click Verify after DNS is in place.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = process.env.TIKTOK_APP_ID || '7630494700880906241';
const CLICK_VERIFY = process.env.CLICK_VERIFY === '1';
const DOMAIN = process.env.TIKTOK_VERIFY_DOMAIN || 'cloudless.gr';

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

function extractToken(s) {
  if (!s) return null;
  const m = s.match(/tiktok-domain-verification=[A-Za-z0-9._-]+/);
  if (m) return m[0];
  const m2 = s.match(/tiktok-domain-verification[=:\s]+([A-Za-z0-9._-]{16,})/i);
  if (m2) return `tiktok-domain-verification=${m2[1]}`;
  return null;
}

async function login(page) {
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
  if (page.url().includes('/login')) throw new Error('login_failed');
}

async function openUrlProperties(page) {
  const candidates = [
    page.getByRole('button', { name: /URL properties/i }),
    page.getByText(/^URL properties$/i),
    page.getByRole('button', { name: /Verify domains/i }),
    page.getByText(/Verify domains/i),
    page.getByRole('button', { name: /^Verify$/i }),
  ];
  for (const loc of candidates) {
    const first = loc.first();
    if (await first.isVisible().catch(() => false)) {
      await first.click({ force: true }).catch(() => {});
      await page.waitForTimeout(2500);
      await dismissCookies(page);
      return true;
    }
  }
  // Evaluate fallback
  return page.evaluate(() => {
    const el = [...document.querySelectorAll('button, a, [role="button"], span')]
      .find((e) => /URL properties|Verify domains/i.test(e.textContent || '') && (e.textContent || '').length < 40);
    if (el) { el.click(); return true; }
    return false;
  });
}

async function harvestToken(page) {
  const text = await page.locator('body').innerText();
  const html = await page.content();
  let token = extractToken(text) || extractToken(html);
  if (token) return token;

  // input/textarea values
  const inputs = page.locator('input, textarea');
  const n = await inputs.count();
  for (let i = 0; i < n; i++) {
    const val = (await inputs.nth(i).inputValue().catch(() => '')) || '';
    token = extractToken(val);
    if (token) return token;
    if (/^[A-Za-z0-9._-]{20,}$/.test(val) && !/http|cloudless|@/.test(val)) {
      return `tiktok-domain-verification=${val}`;
    }
  }

  // code/pre
  const codes = await page.locator('code, pre').allTextContents().catch(() => []);
  token = extractToken(codes.join('\n'));
  return token;
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

try {
  await login(page);
  // App page (draft or pending) — URL properties lives in header/nav
  for (const u of [
    `https://developers.tiktok.com/app/${APP_ID}`,
    `https://developers.tiktok.com/app/${APP_ID}/pending`,
  ]) {
    await page.goto(u, { waitUntil: 'domcontentloaded', timeout: 120000 });
    await page.waitForTimeout(4000);
    await dismissCookies(page);
    if (/Cloudless|URL properties|Content Posting/i.test(await page.locator('body').innerText())) break;
  }
  await shot(page, 'dv-app.png');

  const opened = await openUrlProperties(page);
  await shot(page, 'dv-panel.png');
  let text = await page.locator('body').innerText();

  // Close AI assistant if it stole focus
  await page.getByRole('button', { name: /Close/i }).first().click({ force: true }).catch(() => {});
  await page.waitForTimeout(500);

  // If property type picker: choose Domain
  if (/Select property type/i.test(text)) {
    await page.getByText(/^Domain$/).first().click({ force: true }).catch(() => {});
    await page.waitForTimeout(800);
    const next = page.getByRole('button', { name: /next|continue|add|confirm|create/i }).first();
    if (await next.isEnabled().catch(() => false)) await next.click({ force: true });
    await page.waitForTimeout(2000);
    text = await page.locator('body').innerText();
  }

  // Prefer clicking existing cloudless.gr row
  const domainRow = page.getByText(DOMAIN, { exact: true }).first();
  if (await domainRow.isVisible().catch(() => false)) {
    await domainRow.click({ force: true });
    await page.waitForTimeout(2000);
  } else {
    // Add domain carefully — only editable inputs that look like domain fields
    const addBtn = page.locator('button').filter({ hasText: /add|create|new/i });
    for (let i = 0; i < await addBtn.count(); i++) {
      const b = addBtn.nth(i);
      if (await b.isVisible().catch(() => false) && await b.isEnabled().catch(() => false)) {
        const label = (await b.innerText().catch(() => '')).slice(0, 40);
        if (/URI|redirect|scope/i.test(label)) continue;
        await b.click({ force: true });
        await page.waitForTimeout(1200);
        break;
      }
    }
    await page.getByText(/^Domain$/).first().click({ force: true }).catch(() => {});
    const inputs = page.locator('input[type="text"], input[type="url"], input:not([type])');
    const ic = await inputs.count();
    for (let i = 0; i < ic; i++) {
      const el = inputs.nth(i);
      if (!(await el.isVisible().catch(() => false))) continue;
      if (await el.isDisabled().catch(() => true)) continue;
      const ph = ((await el.getAttribute('placeholder')) || '').toLowerCase();
      const cur = ((await el.inputValue().catch(() => '')) || '').trim();
      if (/search/i.test(ph)) continue;
      if (cur && !/domain|example|cloudless|http|\./i.test(cur + ph)) continue;
      // Skip app name / long descriptions
      if (cur === 'Cloudless' || cur.length > 80) continue;
      await el.fill(DOMAIN);
      await page.waitForTimeout(400);
      break;
    }
    const conf = page.getByRole('button', { name: /add|confirm|create|save|next|continue/i }).first();
    if (await conf.isEnabled().catch(() => false)) {
      await conf.click({ force: true });
      await page.waitForTimeout(3000);
    }
  }
  await shot(page, 'dv-domain-detail.png');

  // Expand DNS / copy UI
  for (const label of [/DNS record/i, /TXT/i, /Copy/i, /Show record/i, /verification record/i, /Get record/i, /Verify properties/i]) {
    const el = page.getByText(label).first();
    if (await el.isVisible().catch(() => false)) await el.click({ force: true }).catch(() => {});
  }
  await page.waitForTimeout(1200);

  // Click Verify properties (opens DNS instructions) without final Verify yet
  const vp = page.getByRole('button', { name: /Verify properties/i }).first();
  if (await vp.isVisible().catch(() => false)) {
    await vp.click({ force: true }).catch(() => {});
    await page.waitForTimeout(2500);
  }

  let token = await harvestToken(page);
  await shot(page, 'dv-token-hunt.png');
  write('dv-body-snippet.json', {
    opened,
    snippet: (await page.locator('body').innerText()).slice(0, 8000),
    hasToken: Boolean(token),
  });

  if (token) {
    write('domain-verification-token.json', { token, domain: DOMAIN });
    console.log('FOUND_DOMAIN_TOKEN');
  } else {
    write('domain-verification-token.json', { token: null, domain: DOMAIN });
    console.log('NO_DOMAIN_TOKEN');
  }

  if (CLICK_VERIFY) {
    const buttons = page.getByRole('button', { name: /^Verify$|Verify properties|Verify domain/i });
    for (let i = 0; i < await buttons.count(); i++) {
      const b = buttons.nth(i);
      if (await b.isVisible().catch(() => false) && await b.isEnabled().catch(() => false)) {
        await b.click({ force: true }).catch(() => {});
        await page.waitForTimeout(7000);
      }
    }
    await shot(page, 'dv-verified.png');
    const after = await page.locator('body').innerText();
    write('domain-verify-result.json', {
      url: page.url(),
      verifiedHint: /verified/i.test(after) && !/unverified|not verified/i.test(after),
      snippet: after.slice(0, 5000),
    });
    console.log('CLICKED_VERIFY');
  }

  write('domain-verify-summary.json', {
    url: page.url(),
    hasToken: Boolean(token),
    clickVerify: CLICK_VERIFY,
  });
  console.log('DOMAIN_VERIFY_DONE');
} catch (e) {
  write('domain-verify-error.json', { error: String(e) });
  console.error('ERROR', String(e));
  await browser.close();
  process.exit(1);
}

await browser.close();
