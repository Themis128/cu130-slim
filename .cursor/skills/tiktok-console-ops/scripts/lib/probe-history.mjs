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

await page.goto(`https://developers.tiktok.com/app/${APP_ID}/live`, { waitUntil: 'domcontentloaded' }).catch(() => {});
await page.waitForTimeout(4000); await dismissCookies(page);

// find history icon/button near top of app page
const hist = page.locator('a[href*="history"], button[aria-label*="istor"], [class*="history"], [class*="History"]').first();
const res = { histVisible: await hist.isVisible().catch(() => false) };
// dump all links to find history URL
res.links = await page.evaluate(() => [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).filter(h => /app|history|review/i.test(h || '')));
if (res.histVisible) {
  await hist.click({ force: true }).catch(() => {});
  await page.waitForTimeout(4000); await dismissCookies(page);
  await shot(page, 'probe-history.png');
  res.historyUrl = page.url();
  res.historyText = (await page.locator('body').innerText()).slice(0, 10000);
} else {
  for (const u of [`https://developers.tiktok.com/app/${APP_ID}/history`, `https://developers.tiktok.com/app/${APP_ID}/live?tab=history`]) {
    await page.goto(u, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
    await page.waitForTimeout(3500);
    const txt = await page.locator('body').innerText();
    if (!/Something went wrong/i.test(txt)) { res.historyUrl = page.url(); res.historyText = txt.slice(0, 10000); await shot(page, 'probe-history.png'); break; }
  }
}
write('probe-history.json', res);
console.log(JSON.stringify({ histVisible: res.histVisible, historyUrl: res.historyUrl, links: (res.links||[]).slice(0,30), snippet: (res.historyText||'').slice(0, 4000) }, null, 2));
await browser.close();
