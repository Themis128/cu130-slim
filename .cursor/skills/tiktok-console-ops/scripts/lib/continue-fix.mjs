/**
 * One-shot: dismiss cookies, return-to-draft if needed, set Website URL to
 * https://cloudless.gr, open Content Posting Verify / URL properties for DNS token,
 * optionally submit for review (SUBMIT_REVIEW=1).
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

async function nukeOverlays(page) {
  for (let i = 0; i < 6; i++) {
    await page.evaluate(() => {
      const click = (re) => {
        const t = [...document.querySelectorAll('button, [role="button"], a')]
          .find((b) => re.test(b.textContent || ''));
        if (t) t.click();
      };
      click(/allow all/i);
      click(/accept all/i);
      // Remove common overlay nodes
      for (const sel of [
        '[class*="cookie"]', '[id*="cookie"]', '[class*="Cookie"]',
        '[class*="banner"]', '[id*="onetrust"]', '.ot-sdk-container',
      ]) {
        document.querySelectorAll(sel).forEach((el) => {
          if (/cookie|consent|allow all/i.test(el.textContent || el.className || '')) {
            el.remove();
          }
        });
      }
    }).catch(() => {});
    await page.waitForTimeout(400);
  }
}

function extractToken(s) {
  if (!s) return null;
  const m = s.match(/tiktok-domain-verification=[A-Za-z0-9._-]+/);
  if (m) return m[0];
  const m2 = s.match(/tiktok-domain-verification[=:\s]+([A-Za-z0-9._-]{16,})/i);
  return m2 ? `tiktok-domain-verification=${m2[1]}` : null;
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
  return extractToken((await page.locator('code, pre').allTextContents().catch(() => [])).join('\n'));
}

async function listInputs(page) {
  const out = [];
  const loc = page.locator('input[type="text"], input[type="url"], input:not([type]), textarea');
  for (let i = 0; i < await loc.count(); i++) {
    const el = loc.nth(i);
    if (!(await el.isVisible().catch(() => false))) continue;
    out.push({
      disabled: await el.isDisabled().catch(() => true),
      value: ((await el.inputValue().catch(() => '')) || '').trim(),
    });
  }
  return out;
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
await nukeOverlays(page);
await page.getByPlaceholder('Email').fill(EMAIL);
await page.getByPlaceholder('Password').fill(PASSWORD);
await nukeOverlays(page);
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
await nukeOverlays(page);
if (page.url().includes('/login')) {
  write('continue-fix.json', { ok: false, error: 'login_failed' });
  process.exit(2);
}
report.steps.push('login');

await page.goto(`https://developers.tiktok.com/app/${APP_ID}/pending`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(4000);
await nukeOverlays(page);
await shot(page, 'cf-01.png');
report.before = await listInputs(page);

// Return to draft if fields locked / rejected banner
const needDraft = report.before.some((i) => /social\.cloudless\.gr\/?$/.test(i.value) && i.disabled)
  || /Not approved|Return to Draft/i.test(await page.locator('body').innerText());
if (needDraft) {
  const returned = await page.evaluate(() => {
    const el = [...document.querySelectorAll('button, a, [role="button"]')]
      .find((e) => /Return to Draft/i.test(e.textContent || ''));
    if (el) { el.click(); return true; }
    return false;
  });
  report.steps.push(returned ? 'return_to_draft' : 'no_return_btn');
  await page.waitForTimeout(2000);
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')]
      .find((e) => /^Confirm$/i.test((e.textContent || '').trim()));
    if (b) b.click();
  });
  await page.waitForTimeout(3500);
  await nukeOverlays(page);
}
await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
}).catch(() => {});
await page.waitForTimeout(3500);
await nukeOverlays(page);
await shot(page, 'cf-02-draft.png');

// Set website URL
let updated = 0;
const changes = [];
const inputs = page.locator('input[type="text"], input[type="url"], input:not([type])');
for (let i = 0; i < await inputs.count(); i++) {
  const el = inputs.nth(i);
  if (!(await el.isVisible().catch(() => false))) continue;
  if (await el.isDisabled().catch(() => true)) continue;
  const cur = ((await el.inputValue().catch(() => '')) || '').trim();
  if (/^https?:\/\/(www\.)?social\.cloudless\.gr\/?$/i.test(cur)
    || /^https?:\/\/(www\.)?(social\.)?cloudless\.(jp|app)\/?$/i.test(cur)) {
    await el.fill(WEB);
    updated += 1;
    changes.push({ from: cur, to: WEB });
  }
}
report.updated = updated;
report.changes = changes;
report.steps.push(updated ? `web_updated_${updated}` : 'web_already_ok_or_locked');

// Save
const saved = await page.evaluate(() => {
  const b = [...document.querySelectorAll('button')]
    .find((e) => /^(Save|Save changes)$/i.test((e.textContent || '').trim())
      || /^Apply$/i.test((e.textContent || '').trim()));
  if (b && !b.disabled) { b.click(); return true; }
  return false;
});
report.saved = saved;
if (saved) await page.waitForTimeout(3000);
await nukeOverlays(page);
report.afterWeb = (await listInputs(page)).filter((i) => i.value);
await shot(page, 'cf-03-web-saved.png');

// Domain verify: click Content Posting Verify OR URL properties
await nukeOverlays(page);
const openedVerify = await page.evaluate(() => {
  // Prefer the Verify next to "Verify domains"
  const labels = [...document.querySelectorAll('*')].filter((e) =>
    /Verify domains/i.test(e.textContent || '') && (e.textContent || '').length < 80);
  for (const lab of labels) {
    const root = lab.closest('section, div, li') || lab.parentElement;
    if (!root) continue;
    const btn = [...root.querySelectorAll('button, a')].find((b) => /^Verify$/i.test((b.textContent || '').trim()));
    if (btn) { btn.click(); return 'content_posting_verify'; }
  }
  const up = [...document.querySelectorAll('button, a, span')]
    .find((e) => (e.textContent || '').trim() === 'URL properties');
  if (up) { up.click(); return 'url_properties'; }
  return null;
});
report.steps.push(`opened:${openedVerify}`);
await page.waitForTimeout(2500);
await nukeOverlays(page);
await shot(page, 'cf-04-verify-modal.png');

// If Select property type
let body = await page.locator('body').innerText();
if (/Select property type/i.test(body)) {
  await page.evaluate(() => {
    const el = [...document.querySelectorAll('button, div, label, li')]
      .find((e) => {
        const t = (e.textContent || '').trim();
        return (t === 'Domain' || /^Domain\n/i.test(t) || (t.startsWith('Domain') && /DNS/i.test(t)))
          && t.length < 160;
      });
    if (el) el.click();
  });
  await page.waitForTimeout(600);
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')]
      .find((e) => /next|continue|confirm|add|create/i.test(e.textContent || '')
        && !/cancel|close/i.test(e.textContent || '') && !e.disabled);
    if (b) b.click();
  });
  await page.waitForTimeout(2000);
  report.steps.push('selected_domain');
  await nukeOverlays(page);
}

// Click cloudless.gr + Verify properties
await page.evaluate((domain) => {
  const row = [...document.querySelectorAll('*')].find((e) =>
    (e.textContent || '').trim() === domain && e.children.length <= 1);
  if (row) row.click();
}, 'cloudless.gr');
await page.waitForTimeout(1000);
await page.evaluate(() => {
  const b = [...document.querySelectorAll('button, a')]
    .find((e) => /Verify properties/i.test(e.textContent || ''));
  if (b) b.click();
});
await page.waitForTimeout(2500);
await nukeOverlays(page);

// Fill domain if needed
await page.evaluate((domain) => {
  for (const inp of document.querySelectorAll('input')) {
    if (inp.disabled || !inp.offsetParent) continue;
    const ph = (inp.placeholder || '').toLowerCase();
    const val = (inp.value || '').trim();
    if (/search/i.test(ph) || val === 'Cloudless' || val.length > 60) continue;
    if (!val || /domain|example|http/i.test(ph)) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(inp, domain);
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      inp.dispatchEvent(new Event('change', { bubbles: true }));
      break;
    }
  }
}, 'cloudless.gr');
await page.evaluate(() => {
  const b = [...document.querySelectorAll('button')]
    .find((e) => /add|confirm|create|next|continue/i.test(e.textContent || '')
      && !/cancel|close/i.test(e.textContent || '') && !e.disabled);
  if (b) b.click();
});
await page.waitForTimeout(2500);

// Expand DNS
await page.evaluate(() => {
  for (const re of [/DNS/i, /TXT/i, /Copy/i, /Show record/i]) {
    const el = [...document.querySelectorAll('button, a, span')]
      .find((e) => re.test(e.textContent || '') && (e.textContent || '').length < 36);
    if (el) el.click();
  }
});
await page.waitForTimeout(1500);
await shot(page, 'cf-05-dns.png');

const token = await harvest(page);
if (token) {
  write('domain-verification-token.json', { token, domain: 'cloudless.gr' });
  report.hasToken = true;
  report.steps.push('token_found');
} else {
  write('domain-verification-token.json', { token: null, domain: 'cloudless.gr' });
  report.hasToken = false;
  report.steps.push('token_missing');
}
write('cf-modal-snippet.json', {
  snippet: (await page.locator('body').innerText()).slice(0, 8000),
  hasToken: Boolean(token),
});

// Close modal if open, then submit
await page.keyboard.press('Escape').catch(() => {});
await page.waitForTimeout(800);
await nukeOverlays(page);

const webOk = (await listInputs(page)).some((i) => /^https?:\/\/(www\.)?cloudless\.gr\/?$/i.test(i.value));
report.websiteOk = webOk;

let submitted = false;
if (SUBMIT) {
  // Re-open app page clean
  await page.goto(`https://developers.tiktok.com/app/${APP_ID}`, {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  });
  await page.waitForTimeout(3500);
  await nukeOverlays(page);
  // Ensure website still cloudless.gr
  for (let i = 0; i < await inputs.count(); i++) {
    const el = inputs.nth(i);
    if (!(await el.isVisible().catch(() => false))) continue;
    if (await el.isDisabled().catch(() => true)) continue;
    const cur = ((await el.inputValue().catch(() => '')) || '').trim();
    if (/^https?:\/\/(www\.)?social\.cloudless\.gr\/?$/i.test(cur)) {
      await el.fill(WEB);
      await page.evaluate(() => {
        const b = [...document.querySelectorAll('button')]
          .find((e) => /^Save$/i.test((e.textContent || '').trim()));
        if (b && !b.disabled) b.click();
      });
      await page.waitForTimeout(2500);
    }
  }
  const canSubmit = await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')]
      .find((e) => /Submit for review/i.test(e.textContent || '') && !e.disabled);
    if (b) { b.click(); return true; }
    return false;
  });
  report.steps.push(canSubmit ? 'clicked_submit' : 'submit_not_found');
  if (canSubmit) {
    await page.waitForTimeout(2000);
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('button')]
        .find((e) => /confirm|submit|yes|continue/i.test(e.textContent || '')
          && !/cancel|close|back/i.test(e.textContent || '') && !e.disabled);
      if (b) b.click();
    });
    await page.waitForTimeout(4000);
    submitted = true;
  }
  await shot(page, 'cf-06-submit.png');
}

const finalText = await page.locator('body').innerText();
report.submitted = submitted;
report.underReview = /Under review/i.test(finalText);
report.url = page.url();
report.after = (await listInputs(page)).filter((i) => i.value).slice(0, 20);
write('continue-fix.json', report);
console.log(JSON.stringify({
  ok: true,
  updated,
  saved,
  websiteOk: webOk,
  hasToken: Boolean(token),
  submitted,
  underReview: report.underReview,
  steps: report.steps,
  changes,
}, null, 2));
await browser.close();
