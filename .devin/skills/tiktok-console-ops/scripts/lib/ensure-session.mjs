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

// Optional proxy (e.g. TIKTOK_LOGIN_PROXY=socks5://127.0.0.1:1080 for WARP) —
// TikTok rate-limits password logins per IP+account, so a different egress IP
// can clear "Maximum number of attempts reached".
const PROXY = process.env.TIKTOK_LOGIN_PROXY;
const browser = await chromium.launch({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled', '--no-sandbox'],
  ...(PROXY ? { proxy: { server: PROXY } } : {}),
});
if (PROXY) console.error(`[ensure-session] using proxy ${PROXY}`);
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
});
const page = await context.newPage();

async function dismissCookies() {
  for (const name of [/allow all/i, /accept all/i, /agree/i]) {
    const btn = page.getByRole('button', { name });
    if (await btn.first().isVisible().catch(() => false)) {
      await btn.first().click({ timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(800);
      return;
    }
  }
}

async function shot(name) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`) }).catch(() => {});
}

await page.goto('https://www.tiktok.com/login/phone-or-email/email', {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(2000);
await dismissCookies();
await shot('10-tiktok-login');

let cookies = await context.cookies('https://www.tiktok.com');
let session = cookies.find((c) => c.name === 'sessionid');

if (!session?.value) {
  const userInput = page.locator('input[type="text"], input[name="username"]').first();
  const passInput = page.locator('input[type="password"]').first();
  await userInput.waitFor({ state: 'visible', timeout: 15000 });
  await userInput.click();
  await userInput.fill(EMAIL);
  await passInput.click();
  await passInput.fill(PASSWORD);
  await dismissCookies();

  // Prefer the form Log in button (not social SSO).
  const loginBtn = page.getByRole('button', { name: /^log in$/i }).last();
  await loginBtn.click({ timeout: 10000 }).catch(async () => {
    await page.locator('button[type="submit"]').first().click();
  });
  await page.waitForTimeout(10000);
  await shot('11-after-tiktok-login');

  const body = await page.locator('body').innerText().catch(() => '');
  if (/captcha|verify|security code|two-step|slider/i.test(body)) {
    fs.writeFileSync(
      path.join(OUT, 'sidecar-login-blocker.json'),
      JSON.stringify({ blocker: 'captcha_or_2fa', url: page.url() }, null, 2),
    );
    console.error('LOGIN_BLOCKED_CAPTCHA_OR_2FA');
    await browser.close();
    process.exit(3);
  }

  // Warm session on home/profile
  await page.goto('https://www.tiktok.com/', {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  }).catch(() => {});
  await page.waitForTimeout(3000);
  await dismissCookies();
  await page.goto('https://www.tiktok.com/@user3113682023385', {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  }).catch(() => {});
  await page.waitForTimeout(3000);
  await shot('12-profile-after-login');

  cookies = await context.cookies('https://www.tiktok.com');
  session = cookies.find((c) => c.name === 'sessionid');
}

if (!session?.value) {
  fs.writeFileSync(
    path.join(OUT, 'sidecar-login-blocker.json'),
    JSON.stringify({
      blocker: 'no_session_cookie',
      url: page.url(),
      cookie_names: cookies.map((c) => c.name),
    }, null, 2),
  );
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
console.log(
  JSON.stringify({
    httpStatus: res.status,
    logged_in: body.logged_in,
    profile_url: body.profile_url,
  }),
);
await browser.close();
process.exit(body.logged_in ? 0 : 5);
