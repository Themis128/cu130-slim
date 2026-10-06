import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const OUT = process.env.OUT_DIR || '/out';
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
const results = { steps: [] };
// notifications page candidates
for (const u of ['https://developers.tiktok.com/notifications', 'https://developers.tiktok.com/notification', 'https://developers.tiktok.com/user/notifications']) {
  await page.goto(u, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(3500); await dismissCookies(page);
  results.steps.push({ url: u, landed: page.url(), text: (await page.locator('body').innerText()).slice(0, 6000) });
  if (!/Something went wrong/i.test(results.steps.at(-1).text)) break;
}
await shot(page, 'probe-notif-page.png');
write('probe-notifs.json', results);
const t = results.steps.at(-1);
console.log(JSON.stringify({ landed: t.landed, snippet: t.text.slice(0, 3500) }, null, 2));
await browser.close();
