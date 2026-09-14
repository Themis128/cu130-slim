/**
 * Configure Cloudless TikTok app per official docs:
 * - Dismiss cookie banner
 * - Fix Web / Login Kit URLs to social.cloudless.gr SocialAuto paths
 * - Open Content Posting domain verification and capture token
 * - Optionally click Verify (CLICK_VERIFY=1)
 *
 * Never prints secrets.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = process.env.TIKTOK_APP_ID || '7630494700880906241';
const CLICK_VERIFY = process.env.CLICK_VERIFY === '1';
const FIX_URLS = process.env.FIX_URLS !== '0';

const EXPECTED = {
  webUrl: 'https://social.cloudless.gr',
  redirect: 'https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback',
  domain: 'cloudless.gr',
  tos: 'https://cloudless.gr/terms',
  privacy: 'https://cloudless.gr/privacy',
};

fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});

async function dismissCookies(page) {
  for (let i = 0; i < 3; i++) {
    const clicked = await page.evaluate(() => {
      const btns = [...document.querySelectorAll('button')];
      const t = btns.find((b) => /allow all/i.test(b.textContent || ''));
      if (t) {
        t.click();
        return true;
      }
      return false;
    });
    if (clicked) await page.waitForTimeout(800);
    else break;
  }
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

async function fillMatchingInputs(page, needleRe, value) {
  const inputs = page.locator('input[type="text"], input[type="url"], input:not([type]), textarea');
  const n = await inputs.count();
  let filled = 0;
  for (let i = 0; i < n; i++) {
    const el = inputs.nth(i);
    if (!(await el.isVisible().catch(() => false))) continue;
    if (await el.isDisabled().catch(() => true)) continue;
    const cur = (await el.inputValue().catch(() => '')) || '';
    if (cur.trim() === value.trim()) continue;
    if (needleRe.test(cur)) {
      await el.fill(value, { timeout: 5000 }).catch(() => {});
      filled += 1;
    }
  }
  return filled;
}

async function openVerifyPanel(page) {
  await dismissCookies(page);
  // Scroll toward Content Posting
  await page.evaluate(() => {
    const el = [...document.querySelectorAll('*')].find((e) =>
      /Content Posting API|Verify domains/i.test(e.textContent || '') && (e.textContent || '').length < 80,
    );
    if (el) el.scrollIntoView({ block: 'center' });
  });
  await page.waitForTimeout(1000);
  await dismissCookies(page);

  const candidates = [
    page.getByRole('button', { name: /^Verify$/i }),
    page.getByRole('button', { name: /Verify domains/i }),
    page.getByText(/^Verify$/),
    page.getByText(/Verify domains/i),
  ];
  for (const loc of candidates) {
    if (await loc.count()) {
      const first = loc.first();
      if (await first.isVisible().catch(() => false)) {
        await first.click({ force: true }).catch(() => {});
        await page.waitForTimeout(2500);
        await dismissCookies(page);
        return true;
      }
    }
  }
  return false;
}

function extractToken(text) {
  const m = text.match(/tiktok-domain-verification=[A-Za-z0-9._-]+/);
  return m ? m[0] : null;
}

if (!EMAIL || !PASSWORD) {
  console.error('Missing TIKTOK_DEV_EMAIL / TIKTOK_DEV_PASSWORD');
  process.exit(1);
}

const browser = await chromium.launch({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled'],
});
const page = await (await browser.newContext({ viewport: { width: 1440, height: 1100 } })).newPage();
const report = { steps: {}, expected: EXPECTED };

try {
  await login(page);
  await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  });
  await page.waitForTimeout(5000);
  await dismissCookies(page);
  // Prefer pending/production config page if redirected to list
  if (page.url().includes('/apps')) {
    await page.goto(`https://developers.tiktok.com/app/${APP_ID}/pending`, {
      waitUntil: 'domcontentloaded',
      timeout: 120000,
    });
    await page.waitForTimeout(4000);
    await dismissCookies(page);
  }
  await shot(page, 'cfg-01-app.png');
  report.url = page.url();

  if (FIX_URLS) {
    const filledRedirect = await fillMatchingInputs(
      page,
      /cloudless\.(jp|app)|email\.cloudless|redirect|oauth\/tiktok/i,
      EXPECTED.redirect,
    );
    // Also set any field that looks like a site URL but wrong TLD
    const filledWeb = await fillMatchingInputs(
      page,
      /^https?:\/\/(www\.)?(social\.)?cloudless\.(jp|app)/i,
      EXPECTED.webUrl,
    );
    // Specific known bad values
    const filledEmailHost = await fillMatchingInputs(page, /email\.cloudless\.app/i, EXPECTED.webUrl);
    report.steps.urlFix = { filledRedirect, filledWeb, filledEmailHost };

    // Save / Apply if present (do not submit for review)
    const saveBtn = page.getByRole('button', { name: /save|apply|update/i }).first();
    if (await saveBtn.count() && (await saveBtn.isEnabled().catch(() => false))) {
      await saveBtn.click({ force: true });
      await page.waitForTimeout(3000);
      report.steps.saved = true;
    } else {
      report.steps.saved = false;
    }
    await dismissCookies(page);
    await shot(page, 'cfg-02-urls.png');
  }

  const opened = await openVerifyPanel(page);
  report.steps.verifyPanelOpened = opened;
  await shot(page, 'cfg-03-verify-panel.png');

  let text = await page.locator('body').innerText();
  let token = extractToken(text);

  if (!token) {
    // Try add domain flow inside modal/panel
    const addBtn = page.locator('button').filter({ hasText: /add|create|new/i });
    const n = await addBtn.count();
    for (let i = 0; i < n; i++) {
      const b = addBtn.nth(i);
      if ((await b.isVisible().catch(() => false)) && (await b.isEnabled().catch(() => false))) {
        await b.click({ force: true });
        await page.waitForTimeout(1200);
        break;
      }
    }
    await page.getByText(/^Domain$/).first().click().catch(() => {});
    const inputs = page.locator('input[type="text"], input:not([type]), input[type="url"]');
    const ic = await inputs.count();
    for (let i = 0; i < ic; i++) {
      const el = inputs.nth(i);
      if (!(await el.isVisible().catch(() => false))) continue;
      const ph = ((await el.getAttribute('placeholder')) || '').toLowerCase();
      const val = (await el.inputValue().catch(() => '')) || '';
      if (!val || /domain|example|cloudless|http/.test(ph + val)) {
        await el.fill(EXPECTED.domain);
        break;
      }
    }
    const conf = page.getByRole('button', { name: /add|confirm|create|save|next|continue|submit/i }).first();
    if (await conf.count() && (await conf.isEnabled().catch(() => false))) {
      await conf.click({ force: true });
      await page.waitForTimeout(3500);
    }
    await shot(page, 'cfg-04-after-add-domain.png');
    text = await page.locator('body').innerText();
    token = extractToken(text);
  }

  // Also scan page HTML for verification string
  if (!token) {
    const html = await page.content();
    token = extractToken(html);
  }

  if (token) {
    write('domain-verification-token.json', { token, domain: EXPECTED.domain });
    report.domainToken = token;
    console.log('FOUND_DOMAIN_TOKEN');
  } else {
    write('domain-verification-token.json', { token: null, domain: EXPECTED.domain });
    console.log('NO_DOMAIN_TOKEN');
  }

  if (CLICK_VERIFY) {
    const vbtn = page.getByRole('button', { name: /^Verify$/i }).first();
    if (await vbtn.count()) {
      await vbtn.click({ force: true });
      await page.waitForTimeout(7000);
      await shot(page, 'cfg-05-verified.png');
      const after = await page.locator('body').innerText();
      write('domain-verify-result.json', {
        verifiedHint: /verified/i.test(after),
        snippet: after.slice(0, 4000),
      });
      console.log('CLICKED_VERIFY');
    }
  }

  // Final page signals
  text = await page.locator('body').innerText();
  report.final = {
    url: page.url(),
    hasGrRedirect: text.includes(EXPECTED.redirect) || /social\.cloudless\.gr\/api\/v1\/auth\/oauth\/tiktok\/callback/.test(text),
    hasJp: /cloudless\.jp/i.test(text),
    hasCloudlessApp: /cloudless\.app/i.test(text),
    productionNotApproved: /Not approved|not approved/i.test(text),
    directPostOn: /Direct(?:ly)? post/i.test(text),
    hasToken: Boolean(token),
  };
  write('configure-app-report.json', report);
  console.log(JSON.stringify({ ok: true, ...report.final, urlFix: report.steps.urlFix }));
} catch (e) {
  write('configure-app-error.json', { error: String(e) });
  console.error('ERROR', String(e));
  await browser.close();
  process.exit(1);
}

await browser.close();
