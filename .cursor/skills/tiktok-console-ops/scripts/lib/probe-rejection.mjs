import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
const APP_ID = '7630494700880906241';
const write = (n, d) => fs.writeFileSync(path.join(OUT, n), JSON.stringify(d, null, 2));
const shot = async (p, n) => p.screenshot({ path: path.join(OUT, n), fullPage: true }).catch(() => {});
async function dismissCookies(page) {
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll('button, a, [role="button"]')];
    const t = btns.find((b) => /allow all/i.test(b.textContent || ''));
    if (t) t.click();
  }).catch(() => {});
  await page.waitForTimeout(600);
}
const browser = await chromium.launch({ headless: true, args: ['--disable-blink-features=AutomationControlled'] });
const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
await page.goto('https://developers.tiktok.com/login/', { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(1200); await dismissCookies(page);
await page.getByPlaceholder('Email').fill(EMAIL);
await page.getByPlaceholder('Password').fill(PASSWORD);
await page.waitForTimeout(400); await dismissCookies(page);
const loginBtn = page.getByRole('button', { name: /^Log in$/i });
for (let i = 0; i < 20; i++) { if (!(await loginBtn.isDisabled().catch(() => true))) break; await page.waitForTimeout(200); }
await loginBtn.click({ force: true });
await Promise.race([page.waitForURL((u) => !String(u).includes('/login'), { timeout: 45000 }).catch(() => {}), page.waitForTimeout(20000)]);

const results = {};
// 1. notification center
await page.goto('https://developers.tiktok.com/', { waitUntil: 'domcontentloaded' }).catch(() => {});
await page.waitForTimeout(3000); await dismissCookies(page);
const bell = page.locator('[class*="notification"], [class*="bell"], [aria-label*="notif"]').first();
if (await bell.isVisible().catch(() => false)) { await bell.click().catch(() => {}); await page.waitForTimeout(2000); await shot(page, 'probe-notifs.png'); results.notifs = (await page.locator('body').innerText()).slice(0, 4000); }

// 2. audit application page
await page.goto(`https://developers.tiktok.com/app/${APP_ID}/application/content-posting-api`, { waitUntil: 'domcontentloaded', timeout: 120000 }).catch(() => {});
await page.waitForTimeout(5000); await dismissCookies(page);
await shot(page, 'probe-audit-page.png');
results.auditUrl = page.url();
results.auditText = (await page.locator('body').innerText()).slice(0, 8000);

// 3. try clicking Reapply link on live page (opens audit wizard possibly w/ reason)
await page.goto(`https://developers.tiktok.com/app/${APP_ID}/live`, { waitUntil: 'domcontentloaded' }).catch(() => {});
await page.waitForTimeout(4000); await dismissCookies(page);
const reapply = page.getByText(/^Reapply$/).first();
results.reapplyVisible = await reapply.isVisible().catch(() => false);
if (results.reapplyVisible) {
  await reapply.click({ force: true }).catch(() => {});
  await page.waitForTimeout(5000); await dismissCookies(page);
  await shot(page, 'probe-reapply.png');
  results.reapplyUrl = page.url();
  results.reapplyText = (await page.locator('body').innerText()).slice(0, 8000);
}
write('probe-rejection.json', results);
console.log(JSON.stringify({ url: results.auditUrl, reapplyVisible: results.reapplyVisible, reapplyUrl: results.reapplyUrl, auditSnippet: (results.auditText||'').slice(0, 2500), reapplySnippet: (results.reapplyText||'').slice(0, 2500) }, null, 2));
await browser.close();
