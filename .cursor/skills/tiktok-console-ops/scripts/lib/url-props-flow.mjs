/**
 * Complete URL properties → Domain DNS → extract token for cloudless.gr.
 * Aggressively dismisses cookie banner first.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = process.env.TIKTOK_APP_ID || '7630494700880906241';
const DOMAIN = 'cloudless.gr';
const CLICK_VERIFY = process.env.CLICK_VERIFY === '1';

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismissCookies(page) {
  for (let i = 0; i < 5; i++) {
    const clicked = await page.evaluate(() => {
      const nodes = [...document.querySelectorAll('button, [role="button"], a')];
      const t = nodes.find((b) => /allow all|accept all|agree/i.test(b.textContent || ''));
      if (t) { t.click(); return true; }
      return false;
    });
    if (!clicked) break;
    await page.waitForTimeout(800);
  }
  // Hide sticky cookie bar via CSS if still present
  await page.evaluate(() => {
    for (const el of document.querySelectorAll('[class*="cookie"], [id*="cookie"], [class*="Cookie"]')) {
      el.style.display = 'none';
    }
  }).catch(() => {});
}

function extractToken(s) {
  if (!s) return null;
  const m = s.match(/tiktok-domain-verification=[A-Za-z0-9._-]+/);
  if (m) return m[0];
  const m2 = s.match(/tiktok-domain-verification[=:\s]+([A-Za-z0-9._-]{16,})/i);
  if (m2) return `tiktok-domain-verification=${m2[1]}`;
  return null;
}

async function harvest(page) {
  let token = extractToken(await page.locator('body').innerText()) || extractToken(await page.content());
  if (token) return token;
  const inputs = page.locator('input, textarea');
  for (let i = 0; i < await inputs.count(); i++) {
    const val = (await inputs.nth(i).inputValue().catch(() => '')) || '';
    token = extractToken(val);
    if (token) return token;
    if (/^[A-Za-z0-9._-]{20,}$/.test(val) && !/http|@|cloudless/i.test(val)) {
      return `tiktok-domain-verification=${val}`;
    }
  }
  const codes = await page.locator('code, pre, [class*="copy"]').allTextContents().catch(() => []);
  return extractToken(codes.join('\n'));
}

if (!EMAIL || !PASSWORD) process.exit(1);

const browser = await chromium.launch({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled'],
});
const page = await (await browser.newContext({ viewport: { width: 1440, height: 1100 } })).newPage();
const steps = [];

await page.goto('https://developers.tiktok.com/login/', { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(1000);
await dismissCookies(page);
await page.getByPlaceholder('Email').fill(EMAIL);
await page.getByPlaceholder('Password').fill(PASSWORD);
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
  write('url-props-flow.json', { ok: false, error: 'login_failed' });
  process.exit(2);
}
steps.push('login');

await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(4000);
await dismissCookies(page);

// Open URL properties from header
await page.evaluate(() => {
  const el = [...document.querySelectorAll('button, a, span, div')]
    .find((e) => (e.textContent || '').trim() === 'URL properties');
  if (el) el.click();
});
await page.waitForTimeout(2500);
await dismissCookies(page);
steps.push('opened_url_properties');
await shot(page, 'up-01.png');

// Click existing cloudless.gr row if present
const clickedDomain = await page.evaluate((domain) => {
  const el = [...document.querySelectorAll('*')].find((e) =>
    (e.textContent || '').trim() === domain && e.children.length === 0);
  if (el) { el.click(); return true; }
  const el2 = [...document.querySelectorAll('*')].find((e) =>
    (e.textContent || '').includes(domain) && (e.textContent || '').length < 40);
  if (el2) { el2.click(); return true; }
  return false;
}, DOMAIN);
steps.push(clickedDomain ? 'clicked_domain_row' : 'no_domain_row');
await page.waitForTimeout(1500);
await dismissCookies(page);

// Click Verify properties
const vpClicked = await page.evaluate(() => {
  const el = [...document.querySelectorAll('button, a, [role="button"]')]
    .find((e) => /Verify properties/i.test(e.textContent || ''));
  if (el) { el.click(); return true; }
  return false;
});
steps.push(vpClicked ? 'clicked_verify_properties' : 'no_verify_properties');
await page.waitForTimeout(2500);
await dismissCookies(page);
await shot(page, 'up-02.png');

let body = await page.locator('body').innerText();

// Select property type → Domain
if (/Select property type/i.test(body)) {
  await page.evaluate(() => {
    // Prefer the Domain card (DNS), not URL prefix
    const cards = [...document.querySelectorAll('button, [role="button"], div, label, li')];
    const domainCard = cards.find((e) => {
      const t = (e.textContent || '').trim();
      return /^Domain\b/i.test(t) || (t.startsWith('Domain') && /DNS/i.test(t) && t.length < 120);
    });
    if (domainCard) domainCard.click();
  });
  await page.waitForTimeout(800);
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')]
      .find((e) => /next|continue|confirm|add|create|verify/i.test(e.textContent || '')
        && !/cancel|close|back/i.test(e.textContent || ''));
    if (b && !b.disabled) b.click();
  });
  await page.waitForTimeout(2500);
  steps.push('selected_domain_type');
  await dismissCookies(page);
  await shot(page, 'up-03-domain-type.png');
  body = await page.locator('body').innerText();
}

// If domain input appears, fill cloudless.gr (enabled only)
const filled = await page.evaluate((domain) => {
  const inputs = [...document.querySelectorAll('input')].filter((i) => !i.disabled && i.offsetParent);
  for (const inp of inputs) {
    const ph = (inp.placeholder || '').toLowerCase();
    const val = (inp.value || '').trim();
    if (/search/i.test(ph)) continue;
    if (val === 'Cloudless' || val.length > 60) continue;
    if (!val || /domain|example|http|\./i.test(ph + val)) {
      inp.focus();
      inp.value = '';
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      inp.value = domain;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      inp.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
  }
  return false;
}, DOMAIN);
if (filled) {
  steps.push('filled_domain');
  await page.waitForTimeout(500);
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')]
      .find((e) => /add|confirm|create|next|continue|save/i.test(e.textContent || '')
        && !/cancel|close/i.test(e.textContent || '') && !e.disabled);
    if (b) b.click();
  });
  await page.waitForTimeout(3000);
}

// Expand DNS / copy sections
await page.evaluate(() => {
  for (const re of [/DNS/i, /TXT/i, /Copy/i, /Show/i, /record/i]) {
    const el = [...document.querySelectorAll('button, a, summary, span')]
      .find((e) => re.test(e.textContent || '') && (e.textContent || '').length < 40);
    if (el) el.click();
  }
});
await page.waitForTimeout(1500);
await shot(page, 'up-04-dns.png');

let token = await harvest(page);
body = await page.locator('body').innerText();
write('url-props-body.json', { snippet: body.slice(0, 10000), hasToken: Boolean(token), steps });

if (token) {
  write('domain-verification-token.json', { token, domain: DOMAIN });
  console.log('FOUND_DOMAIN_TOKEN');
} else {
  write('domain-verification-token.json', { token: null, domain: DOMAIN });
  console.log('NO_DOMAIN_TOKEN');
}

if (CLICK_VERIFY && token) {
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')]
      .find((e) => /^Verify$/i.test((e.textContent || '').trim()) || /Verify properties/i.test(e.textContent || ''));
    if (b && !b.disabled) b.click();
  });
  await page.waitForTimeout(8000);
  await shot(page, 'up-05-verified.png');
  const after = await page.locator('body').innerText();
  write('domain-verify-result.json', {
    verifiedHint: /verified/i.test(after) && !/unverified/i.test(after),
    snippet: after.slice(0, 4000),
  });
  console.log('CLICKED_VERIFY');
}

write('url-props-flow.json', { ok: true, hasToken: Boolean(token), steps, url: page.url() });
console.log(JSON.stringify({ ok: true, hasToken: Boolean(token), steps }));
await browser.close();
