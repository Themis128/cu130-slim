/**
 * Fill (and optionally submit) the Content Posting API audit application
 * wizard at developers.tiktok.com/application/content-posting-api.
 *
 * 4 steps: General Information → API client information → Supporting
 * documents (MP4 screen recording + DB-fields list) → Review (3 declaration
 * checkboxes; the "Next" button submits).
 *
 * Wizard gotchas (learned 2026-09-29):
 *  - Wizard state is NOT persisted — every run starts at step 1.
 *  - The daily-user estimate is a <button aria-haspopup="listbox">, not an
 *    input; picking it reveals a second required textarea ("explain how you
 *    determined the estimate").
 *  - Submission only completes after ticking 3 declaration checkboxes on
 *    the Review step; "Next" then spins while it uploads/submits — WAIT.
 *  - Console shows "Under review" beside Direct Post once it lands.
 *
 * Env: TIKTOK_DEV_EMAIL, TIKTOK_DEV_PASSWORD, OUT_DIR
 *   TT_AUDIT_VIDEO — path inside container to MP4 demo (default /work/tiktok-demo-v2.mp4)
 * Args: --submit actually submits; default prepares to the Review step only.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const VIDEO = process.env.TT_AUDIT_VIDEO || '/work/tiktok-demo-v2.mp4';
const SUBMIT = process.argv.includes('--submit');
const OUT = process.env.OUT_DIR || '/out';
fs.mkdirSync(OUT, { recursive: true });
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});
const log = (o) => console.log(JSON.stringify(o));

const ORG = {
  fullName: 'Themistoklis Baltzakis',
  orgName: 'cloudless.gr',
  orgSite: 'https://cloudless.gr',
  orgDesc:
    'Cloudless (cloudless.gr) is a multi-user social media management platform. Creators and agencies connect their own TikTok accounts via Login Kit OAuth and use the Content Posting API to publish scheduled videos and photo posts to their own TikTok profiles. Direct Post is used to publish user-authored content directly to the creator\'s own profile; upload-as-draft remains available as a per-post option for creators who prefer TikTok\'s native editor.',
  appId: '7630494700880906241',
  goal:
    'Cloudless lets multiple creators and teams schedule and publish short-form video to their own TikTok profiles from a unified content calendar. The Content Posting API lets our users write, preview, and publish TikTok videos alongside their other channels without manually recreating posts in the TikTok app. Direct Post removes the extra inbox step so scheduled content lands on the profile at the intended time; upload-as-draft stays available when a creator wants TikTok\'s native editor for music and effects. All published content is authored and reviewed by the account owner before scheduling, and every post carries creator-controlled privacy level, comment/duet/stitch toggles, and AIGC/branded-content flags.',
  estimateExplain:
    'We are a new application and have not launched TikTok publishing to our user base yet. The estimate is based on our current active user count publishing scheduled posts on connected platforms: fewer than 10 users per day. We expect modest growth over the coming months and the Less-than-100 range covers our projected usage with headroom.',
  dbFields:
    'open_id (TikTok user ID); access_token and refresh_token (encrypted at rest); token expiry timestamp; granted OAuth scopes; creator username/display_name and avatar_url; publish_id returned by the Content Posting API; publish mode and publish status (processing/succeeded/failed); error codes and log_id values for diagnostics; the post title/caption authored by the user.',
};

async function dismissCookies(page) {
  for (const name of [/decline optional/i, /allow all/i]) {
    const b = page.getByRole('button', { name }).first();
    if (await b.isVisible().catch(() => false)) { await b.click().catch(() => {}); break; }
  }
  await page.waitForTimeout(500);
}
const dumpStep = async (page, tag) => {
  const d = await page.evaluate(() => ({
    text: document.body.innerText.slice(0, 4000),
    fields: [...document.querySelectorAll('input, textarea')].map((f) => ({
      type: f.type, ph: f.placeholder || '', v: (f.value || '').slice(0, 60), vis: !!f.offsetParent,
    })),
    buttons: [...document.querySelectorAll('button')].map((b) => (b.textContent || '').trim()).filter(Boolean).slice(0, 15),
    uploads: document.querySelectorAll('input[type="file"]').length,
  }));
  write(`cp-step-${tag}.json`, d);
  await shot(page, `cp-step-${tag}.png`);
  return d;
};

const browser = await chromium.launch({ headless: true, args: ['--disable-blink-features=AutomationControlled'] });
const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();

await page.goto('https://developers.tiktok.com/login/', { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(1500);
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
if (page.url().includes('/login')) { log({ ok: false, error: 'login_failed' }); process.exit(2); }
log({ login: 'ok' });

await page.goto('https://developers.tiktok.com/application/content-posting-api', { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForSelector('input, textarea', { timeout: 45000 }).catch(() => {});
await page.waitForTimeout(6000);
await dismissCookies(page);

// ── Step 1 ──
const t1 = page.locator('input[type="text"]:visible');
const t1n = await t1.count();
if (t1n >= 1) await t1.nth(0).fill(ORG.fullName);
if (t1n >= 2) await t1.nth(1).fill(ORG.orgName);
if (t1n >= 3) await t1.nth(2).fill(ORG.orgSite);
await page.locator('textarea:visible').first().fill(ORG.orgDesc);
await page.waitForTimeout(400);
await dumpStep(page, '1');
await page.getByRole('button', { name: /^Next$/ }).click();
await page.waitForTimeout(4000);

// ── Step 2 ──
await page.locator('input[type="text"]:visible').first().fill(ORG.appId);
await page.locator('textarea:visible').first().fill(ORG.goal);
// Daily-users select is a <button aria-haspopup="listbox"> sibling of the label
const ddBtn = page.locator('button[aria-haspopup="listbox"]:visible');
if (await ddBtn.count() > 0) {
  const idx = await page.evaluate(() => {
    const btns = [...document.querySelectorAll('button[aria-haspopup="listbox"]')].filter((b) => b.offsetParent);
    const labels = [...document.querySelectorAll('p, div, span, label')].filter((e) => /Approximately how many users/.test(e.innerText || '') && e.children.length < 15);
    if (!labels.length) return 0;
    const lr = labels[0].getBoundingClientRect();
    let best = 0, bestD = 1e9;
    btns.forEach((b, i) => {
      const d = Math.abs(b.getBoundingClientRect().top - lr.top);
      if (d < bestD) { bestD = d; best = i; }
    });
    return best;
  });
  await ddBtn.nth(idx).click();
  await page.waitForTimeout(1200);
}
const opts = await page.locator('[role="option"]:visible').allInnerTexts().catch(() => []);
log({ dropdownOptions: opts });
const optEls = page.locator('[role="option"]:visible');
if (await optEls.count() > 0) {
  const texts = await optEls.allInnerTexts();
  let pick = 0;
  for (let i = 0; i < texts.length; i++) {
    if (/\d|fewer|less/i.test(texts[i])) { pick = i; break; }
  }
  await optEls.nth(pick).click();
  log({ pickedDailyOption: texts[pick] });
}
await page.waitForTimeout(1500);
// The chosen option reveals a required "Explain how you determined the
// daily usage estimate" textarea — fill the first empty non-chat textarea.
const areas = page.locator('textarea:visible');
for (let i = 0; i < await areas.count(); i++) {
  const ph = await areas.nth(i).getAttribute('placeholder').catch(() => '');
  const val = (await areas.nth(i).inputValue().catch(() => '')) || '';
  if (!val.trim() && ph !== 'Message...') { await areas.nth(i).fill(ORG.estimateExplain); break; }
}
await page.waitForTimeout(800);
await dumpStep(page, '2');
await page.getByRole('button', { name: /^Next$/ }).click();
await page.waitForTimeout(4000);
const s3 = await dumpStep(page, '3');
log({ step3_head: s3.text.slice(0, 800), uploads: s3.uploads });

// ── Step 3: Supporting documents ──
const fileInput = page.locator('input[type="file"]').first();
if (await fileInput.count()) {
  await fileInput.setInputFiles(VIDEO);
  await page.waitForTimeout(5000);
  log({ uploaded: VIDEO });
}
const areas3 = page.locator('textarea:visible');
for (let i = 0; i < await areas3.count(); i++) {
  const ph = await areas3.nth(i).getAttribute('placeholder').catch(() => '');
  const val = (await areas3.nth(i).inputValue().catch(() => '')) || '';
  if (!val.trim() && ph !== 'Message...') { await areas3.nth(i).fill(ORG.dbFields); break; }
}
await page.waitForTimeout(800);
await dumpStep(page, '3-filled');
await page.getByRole('button', { name: /^Next$/ }).click();
await page.waitForTimeout(5000);
const s4 = await dumpStep(page, '4-review');
log({ step4_head: s4.text.slice(0, 1500) });

if (!SUBMIT) {
  log({ done: 'prepared_to_review', submit: false, hint: 're-run with --submit to finalize' });
  await browser.close();
  process.exit(0);
}

// ── Step 4: tick declaration checkboxes → Next submits (spinner runs a while) ──
const boxes = page.locator('input[type="checkbox"], [role="checkbox"]');
for (let i = 0; i < await boxes.count(); i++) {
  const b = boxes.nth(i);
  const checked = await b.isChecked().catch(async () => (await b.getAttribute('aria-checked')) === 'true');
  if (!checked) await b.check().catch(async () => b.click({ force: true }).catch(() => {}));
}
await page.waitForTimeout(600);
await page.getByRole('button', { name: /^Next$/ }).click();

// Wait for a terminal state: navigation away from Review, a success/under-review
// banner, or an explicit error — the MP4 uploads server-side so allow 120s.
let terminal = '';
for (let i = 0; i < 60; i++) {
  await page.waitForTimeout(2000);
  const txt = await page.evaluate(() => document.body.innerText).catch(() => '');
  if (/under review|submitted|application received|success/i.test(txt)) { terminal = 'submitted'; break; }
  if (/error|failed|try again/i.test(txt) && !/Review/.test(txt)) { terminal = 'error'; break; }
  // spinner gone but still on Review → validation blocked the submit
  const stillNext = await page.getByRole('button', { name: /^Next$/ }).isEnabled().catch(() => false);
  const onReview = /Review\s+and\s+[Ss]ubmit|Declaration/i.test(txt);
  if (stillNext && onReview && i > 5) { terminal = 'stuck_on_review'; break; }
}
await dumpStep(page, '5-after-submit');
log({ submitTerminal: terminal });

// Cross-check the app page: the console shows "Under review" beside
// Direct Post once the application lands.
await page.goto(`https://developers.tiktok.com/app/${ORG.appId}/`, { waitUntil: 'domcontentloaded', timeout: 120000 }).catch(() => {});
await page.waitForTimeout(6000);
await dismissCookies(page);
const appText = await page.evaluate(() => document.body.innerText).catch(() => '');
const underReview = /Under review/i.test(appText);
await shot(page, '6-app-page.png');
log({ underReview });

await browser.close();
if (terminal === 'submitted' || underReview) {
  log({ ok: true, result: 'audit_under_review' });
  process.exit(0);
}
log({ ok: false, error: 'submit_not_confirmed', terminal });
process.exit(3);
