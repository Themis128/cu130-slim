/**
 * Login to TikTok.com with TIKTOK_DEV_* creds and POST sessionid to sidecar.
 * Never prints the session cookie value.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EMAIL = process.env.TIKTOK_DEV_EMAIL;
const PASSWORD = process.env.TIKTOK_DEV_PASSWORD;
const SIDECAR = process.env.TIKTOK_SIDECAR_URL || 'http://127.0.0.1:9224';
const OUT = process.env.OUT_DIR || '/out';
fs.mkdirSync(OUT, { recursive: true });

if (!EMAIL || !PASSWORD) {
  console.error('Missing TIKTOK_DEV_EMAIL / TIKTOK_DEV_PASSWORD');
  process.exit(1);
}

const browser = await chromium.launch({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled'],
});
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
});
const page = await context.newPage();

await page.goto('https://www.tiktok.com/login/phone-or-email/email', {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(2000);

let cookies = await context.cookies('https://www.tiktok.com');
let session = cookies.find((c) => c.name === 'sessionid');
if (!session?.value) {
  await page.locator('input[type="text"], input[name="username"]').first().fill(EMAIL);
  await page.locator('input[type="password"]').first().fill(PASSWORD);
  await page.locator('button[type="submit"]').first().click();
  await page.waitForTimeout(8000);
  const body = await page.locator('body').innerText().catch(() => '');
  if (/captcha|verify|security code|two-step/i.test(body)) {
    fs.writeFileSync(
      path.join(OUT, 'sidecar-login-blocker.json'),
      JSON.stringify({ blocker: 'captcha_or_2fa', url: page.url() }, null, 2),
    );
    console.error('LOGIN_BLOCKED_CAPTCHA_OR_2FA');
    await browser.close();
    process.exit(3);
  }
  await page.goto('https://www.tiktok.com/tiktokstudio', {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  }).catch(() => {});
  await page.waitForTimeout(3000);
  cookies = await context.cookies('https://www.tiktok.com');
  session = cookies.find((c) => c.name === 'sessionid');
}

if (!session?.value) {
  console.error('NO_SESSION_COOKIE');
  await browser.close();
  process.exit(4);
}

const res = await fetch(`${SIDECAR}/session`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session_id: session.value }),
});
const body = await res.json().catch(() => ({}));
fs.writeFileSync(
  path.join(OUT, 'sidecar-ensure.json'),
  JSON.stringify(
    { httpStatus: res.status, logged_in: body.logged_in, profile_url: body.profile_url },
    null,
    2,
  ),
);
console.log(JSON.stringify({ ok: res.ok, logged_in: body.logged_in, profile_url: body.profile_url }));
await browser.close();
process.exit(res.ok && body.logged_in ? 0 : 5);
