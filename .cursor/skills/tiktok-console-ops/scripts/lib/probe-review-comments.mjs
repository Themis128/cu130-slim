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

await page.goto(`https://developers.tiktok.com/app/${APP_ID}/history`, { waitUntil: 'domcontentloaded' }).catch(() => {});
await page.waitForTimeout(4500); await dismissCookies(page);
const tab = page.getByText(/^Review comments$/).first();
if (await tab.isVisible().catch(() => false)) {
  await tab.click({ force: true });
  await page.waitForTimeout(3500); await dismissCookies(page);
}
await shot(page, 'probe-review-comments.png');
const txt = await page.locator('body').innerText();
write('review-comments.json', { url: page.url(), text: txt });
const i = txt.indexOf('Review comments');
console.log(txt.slice(Math.max(0, i), i + 5000));
await browser.close();
