/**
 * Full domain-verify pass per TikTok media-transfer docs:
 * 1) Login developer portal
 * 2) Open Cloudless Content Posting → Verify domains / URL properties
 * 3) Capture tiktok-domain-verification= token (or add cloudless.gr)
 * Writes domain-verification-token.json for dns-tiktok-txt.sh + optional verify click after DNS.
 *
 * Env: CLICK_VERIFY=1 to click Verify after token is known (run after DNS add).
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
    const btns = [...document.querySelectorAll('button')];
    const t = btns.find((b) => /allow all/i.test(b.textContent || ''));
    if (t) t.click();
  }).catch(() => {});
  await page.waitForTimeout(600);
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
  for (let i = 0; i < 20; i++) {
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

function extractToken(text) {
  const m = text.match(/tiktok-domain-verification=[A-Za-z0-9._-]+/);
  return m ? m[0] : null;
}

const browser = await chromium.launch({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled'],
});
const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();

try {
  await login(page);
  await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  });
  await page.waitForTimeout(5000);
  await dismissCookies(page);
  await shot(page, 'dv-app.png');

  // Prefer Content Posting "Verify" / "Verify domains"
  const verifyDomains = page.getByText(/Verify domains/i).first();
  const urlProps = page.getByText(/URL properties/i).first();
  if (await verifyDomains.count()) {
    // Click nearby Verify button if present
    const v = page.getByRole('button', { name: /^Verify$/i }).first();
    if (await v.count()) await v.click({ force: true }).catch(() => {});
    else await verifyDomains.click({ force: true }).catch(() => {});
  } else if (await urlProps.count()) {
    await urlProps.click({ force: true });
  }
  await page.waitForTimeout(3000);
  await dismissCookies(page);
  await shot(page, 'dv-panel.png');

  let text = await page.locator('body').innerText();
  let token = extractToken(text);

  if (!token) {
    // Try add domain
    const addCandidates = page.locator('button').filter({ hasText: /add|create|new domain|verify domain/i });
    const n = await addCandidates.count();
    for (let i = 0; i < n; i++) {
      const btn = addCandidates.nth(i);
      if (await btn.isEnabled().catch(() => false)) {
        await btn.click({ force: true });
        break;
      }
    }
    await page.waitForTimeout(1500);
    const domainOpt = page.getByText(/^Domain$/).first();
    if (await domainOpt.count()) await domainOpt.click().catch(() => {});
    const inputs = page.locator('input[type="text"], input:not([type]), input[type="url"]');
    const ic = await inputs.count();
    for (let i = 0; i < ic; i++) {
      if (!(await inputs.nth(i).isVisible().catch(() => false))) continue;
      await inputs.nth(i).fill(DOMAIN);
      break;
    }
    const conf = page.getByRole('button', { name: /add|confirm|create|save|next|continue/i }).first();
    if (await conf.count() && (await conf.isEnabled().catch(() => false))) {
      await conf.click({ force: true });
    }
    await page.waitForTimeout(4000);
    await shot(page, 'dv-after-add.png');
    text = await page.locator('body').innerText();
    token = extractToken(text);
  }

  if (token) {
    write('domain-verification-token.json', { token, domain: DOMAIN });
    console.log('FOUND_DOMAIN_TOKEN');
  } else {
    write('domain-verification-token.json', { token: null, domain: DOMAIN, snippet: text.slice(0, 6000) });
    console.log('NO_DOMAIN_TOKEN');
  }

  if (CLICK_VERIFY) {
    const vbtn = page.getByRole('button', { name: /^Verify$/i }).first();
    if (await vbtn.count()) {
      await vbtn.click({ force: true });
      await page.waitForTimeout(6000);
      await shot(page, 'dv-verified.png');
      const after = await page.locator('body').innerText();
      write('domain-verify-result.json', {
        url: page.url(),
        verified: /verified/i.test(after) && !/not verified/i.test(after),
        snippet: after.slice(0, 4000),
      });
      console.log('CLICKED_VERIFY');
    } else {
      console.log('NO_VERIFY_BUTTON');
    }
  }

  write('domain-verify-summary.json', {
    url: page.url(),
    hasToken: Boolean(token),
    clickVerify: CLICK_VERIFY,
    hasWrongJp: /cloudless\.jp/i.test(text),
  });
} catch (e) {
  write('domain-verify-error.json', { error: String(e) });
  console.error('ERROR', String(e));
  await browser.close();
  process.exit(1);
}

await browser.close();
console.log('DOMAIN_VERIFY_DONE');
