/**
 * Sandbox-tab URL properties: verify cloudless.gr for the Sandbox environment.
 * Unaudited apps' Content Posting calls run in sandbox context — production
 * verification does NOT cover them.
 * Env: CLICK_VERIFY=1 to click Verify after DNS TXT is in place.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = '7630494700880906241';
const DOMAIN = 'cloudless.gr';
const CLICK_VERIFY = process.env.CLICK_VERIFY === '1';
fs.mkdirSync(OUT, { recursive: true });

function write(name, obj) {
  fs.writeFileSync(path.join(OUT, name), JSON.stringify(obj, null, 2));
}
async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, name), fullPage: true }).catch(() => {});
}
async function dismissCookies(page) {
  for (const name of [/allow all/i, /accept all/i, /agree/i]) {
    const btn = page.getByRole('button', { name });
    if (await btn.first().isVisible().catch(() => false)) {
      await btn.first().click({ timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(600);
      return;
    }
  }
}
function extractToken(s) {
  if (!s) return null;
  const m = s.match(/tiktok-(?:domain|developers-site)-verification=[A-Za-z0-9._-]+/);
  if (m) return m[0];
  const m2 = s.match(/tiktok-(?:domain|developers-site)-verification[=:\s]+([A-Za-z0-9._-]{16,})/i);
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
  const codes = await page.locator('code, pre').allTextContents().catch(() => []);
  return extractToken(codes.join('\n'));
}

if (!EMAIL || !PASSWORD) {
  console.error('Missing credentials');
  process.exit(1);
}

const browser = await chromium.launch({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled'],
});
const STATE = path.join(OUT, 'dev-console-state.json');
const ctxOpts = { viewport: { width: 1440, height: 1100 } };
if (fs.existsSync(STATE)) ctxOpts.storageState = STATE;
const context = await browser.newContext(ctxOpts);
const page = await context.newPage();

try {
  // Reuse saved console session if still valid
  if (fs.existsSync(STATE)) {
    await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, {
      waitUntil: 'domcontentloaded', timeout: 120000,
    });
    await page.waitForTimeout(3000);
    if (page.url().includes('/login')) await login(page);
  } else {
    await login(page);
  }
  await context.storageState({ path: STATE });
  await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, {
    waitUntil: 'domcontentloaded', timeout: 120000,
  });
  await page.waitForTimeout(4000);
  await dismissCookies(page);

  // Switch to the Sandbox tab (top toggle "Production | Sandbox")
  const sandboxTab = page.getByText(/^Sandbox$/).first();
  if (await sandboxTab.isVisible().catch(() => false)) {
    await sandboxTab.click({ force: true });
    await page.waitForTimeout(3000);
    await dismissCookies(page);
  }
  await shot(page, 'sb-app.png');

  const opened = await openUrlProperties(page);
  await shot(page, 'sb-panel.png');
  let text = await page.locator('body').innerText();

  // Sandbox modal shows "Verify properties" CTA — click it to start verification
  const verifyProps = page.getByRole('button', { name: /verify propert/i }).first();
  if (await verifyProps.isVisible().catch(() => false)) {
    await verifyProps.click({ force: true });
    await page.waitForTimeout(3000);
    await shot(page, 'sb-verify-props.png');
    text = await page.locator('body').innerText();

    // "Select property type" modal → choose Domain (DNS record) → Next
    await page.getByText(/^Domain$/).first().click({ force: true }).catch(() => {});
    await page.waitForTimeout(800);
    const nextBtn = page.getByRole('button', { name: /next|continue|confirm|ok|add/i }).first();
    if (await nextBtn.isEnabled().catch(() => false)) await nextBtn.click({ force: true });
    await page.waitForTimeout(2000);
    await shot(page, 'sb-domain-input.png');

    // Domain entry field → cloudless.gr → modal's Verify button (inline, next to input)
    const dInput = page.locator('input[type="text"], input[type="url"], input:not([type])').last();
    if (await dInput.isVisible().catch(() => false)) {
      await dInput.click();
      await dInput.fill(DOMAIN);
      await page.waitForTimeout(1200); // let the Verify button enable
      // The inline Verify button inside the property-type modal
      const modalVerify = page.locator('div[role="dialog"], .modal, [class*="modal"]').getByRole('button', { name: /^Verify$/i }).first();
      const fallbackVerify = page.getByRole('button', { name: /^Verify$/i }).first();
      const btn = (await modalVerify.isVisible().catch(() => false)) ? modalVerify : fallbackVerify;
      if (await btn.isEnabled().catch(() => false)) {
        await btn.click({ force: true });
      } else {
        await btn.click({ force: true }).catch(() => {});
      }
      await page.waitForTimeout(4000);
      await shot(page, 'sb-token.png');
      text = await page.locator('body').innerText();
    }
  }

  // Already verified?
  const verifiedSection = /verified properties/i.test(text) &&
    new RegExp(`${DOMAIN.replace('.', '\\.')}`, 'i').test(text);
  write('sb-panel-text.json', { opened, snippet: text.slice(0, 4000) });

  // Click the domain row to reveal its token/status
  const domainRow = page.getByText(DOMAIN, { exact: true }).first();
  if (await domainRow.isVisible().catch(() => false)) {
    await domainRow.click({ force: true }).catch(() => {});
    await page.waitForTimeout(2000);
    await shot(page, 'sb-domain-detail.png');
  } else {
    // Add the domain property
    const addBtn = page.getByRole('button', { name: /add|verify propert|new propert/i }).first();
    if (await addBtn.isVisible().catch(() => false)) {
      await addBtn.click({ force: true });
      await page.waitForTimeout(1500);
      await page.getByText(/^Domain$/).first().click({ force: true }).catch(() => {});
      const input = page.locator('input[type="text"], input[type="url"], input:not([type])').last();
      await input.fill(DOMAIN).catch(() => {});
      const confirm = page.getByRole('button', { name: /next|continue|add|confirm|create|ok/i }).first();
      if (await confirm.isEnabled().catch(() => false)) await confirm.click({ force: true });
      await page.waitForTimeout(2000);
      await shot(page, 'sb-domain-added.png');
    }
  }

  const token = await harvestToken(page);
  write('sandbox-domain-token.json', { token, domain: DOMAIN, verifiedSection });

  if (CLICK_VERIFY && token) {
    // The pink Verify button at the bottom of the "Verify Domain" modal
    const verifyBtns = page.getByRole('button', { name: /^Verify$/i });
    const n = await verifyBtns.count();
    for (let i = n - 1; i >= 0; i--) {
      const b = verifyBtns.nth(i);
      if (await b.isVisible().catch(() => false)) {
        await b.click({ force: true });
        await page.waitForTimeout(5000);
        break;
      }
    }
    await shot(page, 'sb-after-verify.png');
    const after = await page.locator('body').innerText();
    write('sb-verify-result.json', {
      verified: /verified/i.test(after) && !/not verified|unverified|verification fails/i.test(after),
      snippet: after.slice(0, 1500),
    });
  }
  console.log(token ? 'TOKEN_FOUND' : 'NO_TOKEN', verifiedSection ? 'ALREADY_VERIFIED' : '');
  await browser.close();
} catch (e) {
  write('sandbox-domain-error.json', { error: String(e) });
  await shot(page, 'sb-error.png');
  await browser.close();
  process.exit(1);
}
