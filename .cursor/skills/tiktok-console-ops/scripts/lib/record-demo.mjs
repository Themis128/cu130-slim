// Record SocialAuto TikTok publish flow for the Direct Post audit demo.
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
const EMAIL = process.env.SOCIAL_ADMIN_EMAIL;
const PASSWORD = process.env.SOCIAL_ADMIN_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const BASE = process.env.SOCIAL_UI_URL || 'http://127.0.0.1:8082';
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n) }).catch(() => {});
const pause = (p, ms) => p.waitForTimeout(ms);

async function selectedLabels(p) {
  return p.evaluate(() =>
    [...document.querySelectorAll('[data-tour="platform-selector"] button[aria-pressed="true"]')]
      .map((e) => e.getAttribute('aria-label')));
}
async function ensureOnlyTikTok(p, maxTries = 12) {
  for (let i = 0; i < maxTries; i++) {
    const labels = await selectedLabels(p);
    if (labels.length === 1 && labels[0].startsWith('TikTok')) return labels;
    await p.evaluate(() => {
      const btns = [...document.querySelectorAll('[data-tour="platform-selector"] button')];
      for (const b of btns) {
        const l = b.getAttribute('aria-label') || '';
        const sel = b.getAttribute('aria-pressed') === 'true';
        if (sel && !l.startsWith('TikTok')) b.click();
      }
      const tt = btns.find((b) => (b.getAttribute('aria-label') || '').startsWith('TikTok'));
      if (tt && tt.getAttribute('aria-pressed') !== 'true') tt.click();
    });
    await p.waitForTimeout(700);
  }
  return selectedLabels(p);
}

const browser = await chromium.launch({ headless: true, args: ['--disable-blink-features=AutomationControlled'] });
const context = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  recordVideo: { dir: '/work/rec', size: { width: 1280, height: 720 } },
});
const page = await context.newPage();
const log = [];

try {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await pause(page, 2500);
  await page.locator('input[type="email"]').fill(EMAIL);
  await page.locator('input[type="password"]').fill(PASSWORD);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 45000 }).catch(() => {});
  await pause(page, 3500);
  await shot(page, 'demo-1-login.png');

  await page.goto(`${BASE}/accounts`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('text=TikTok', { timeout: 30000 }).catch(() => {});
  await pause(page, 2500);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await pause(page, 1200);
  await shot(page, 'demo-2-accounts.png');

  await page.goto(`${BASE}/content/new`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-tour="platform-selector"] button', { timeout: 45000 });
  await pause(page, 2500);

  const sel = await ensureOnlyTikTok(page);
  log.push({ selected: sel });
  // pick the TikTok account chip
  const acct = page.locator('xpath=//p[contains(text(),"Post as (TikTok")]/following-sibling::div//button').first();
  if (await acct.isVisible().catch(() => false)) {
    await acct.click({ force: true }).catch(() => {});
    await pause(page, 1000);
    log.push({ acctText: await acct.innerText().catch(() => null) });
  }
  await shot(page, 'demo-4-platform.png');

  const fileInput = page.locator('input[type="file"][accept*="video"]').first();
  await fileInput.setInputFiles('/work/demo-clip.mp4');
  await pause(page, 6000);
  await shot(page, 'demo-5-media.png');

  const caption = 'Built, not bought: our social automation pipeline runs entirely on self-hosted infrastructure. #cloudless #selfhosted #automation';
  const editor = page.locator('[data-tour="content-editor"] textarea').first();
  await editor.fill(caption);
  await pause(page, 1500);
  await shot(page, 'demo-6-caption.png');

  const modeSel = page.locator('#tiktok-publish-mode');
  log.push({ modeVisible: await modeSel.isVisible().catch(() => false) });
  if (await modeSel.isVisible().catch(() => false)) {
    await modeSel.scrollIntoViewIfNeeded().catch(() => {});
    await pause(page, 800);
    await modeSel.selectOption('DIRECT_POST').catch(() => {});
    await pause(page, 1200);
    for (const cb of await page.locator('input[type="checkbox"]:visible').all()) {
      await cb.check().catch(() => {});
    }
    await pause(page, 800);
  }
  await shot(page, 'demo-7-directpost.png');

  // Safety: only publish if TikTok is the ONLY selected platform
  const finalSel = await selectedLabels(page);
  log.push({ finalSel });
  if (finalSel.length === 1 && finalSel[0].startsWith('TikTok')) {
    const pub = page.getByRole('button', { name: /publish now/i }).first();
    await pub.scrollIntoViewIfNeeded().catch(() => {});
    await pause(page, 800);
    await pub.click().catch(() => {});
    await pause(page, 15000);
    await shot(page, 'demo-8-published.png');
    log.push({ step: 'published', url: page.url() });
  } else {
    log.push({ abort: 'not-only-tiktok' });
    await shot(page, 'demo-8-aborted.png');
  }
} catch (e) {
  log.push({ error: String(e) });
}
write('record-demo.json', { log });
await context.close();
await browser.close();
console.log(JSON.stringify(log));
console.log('videos:', fs.readdirSync('/work/rec').filter((f) => f.endsWith('.webm')));
