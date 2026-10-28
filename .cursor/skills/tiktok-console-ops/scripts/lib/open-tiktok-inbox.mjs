/**
 * Open TikTok (cloudless.gr session) via Docker Playwright and inspect
 * inbox / notifications for a Content Posting API MEDIA_UPLOAD draft.
 *
 * Env:
 *   OUT_DIR — screenshot/json output (default /out)
 *   SESSION_ID — optional sessionid cookie (from sidecar ensure flow)
 *   PROFILE_URL — default https://www.tiktok.com/@user3113682023385
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const OUT = process.env.OUT_DIR || '/out';
const SESSION_ID = process.env.TIKTOK_SESSION_ID || '';
const PROFILE = process.env.PROFILE_URL || 'https://www.tiktok.com/@user3113682023385';
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled', '--no-sandbox'],
});
const context = await browser.newContext({
  viewport: { width: 1400, height: 900 },
  userAgent:
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  locale: 'en-US',
  timezoneId: 'Europe/Athens',
});

if (SESSION_ID) {
  await context.addCookies([
    {
      name: 'sessionid',
      value: SESSION_ID,
      domain: '.tiktok.com',
      path: '/',
      httpOnly: true,
      secure: true,
      sameSite: 'Lax',
    },
  ]);
}

const page = await context.newPage();
const report = { steps: [], logged_in: false, draft_hints: [] };

async function step(name, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForTimeout(3500);
  // Dismiss cookie banner if present
  await page
    .getByRole('button', { name: /accept all|allow all|agree/i })
    .first()
    .click({ timeout: 2000 })
    .catch(() => {});
  const shot = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: shot, fullPage: false });
  const title = await page.title();
  const text = (await page.locator('body').innerText().catch(() => '')).slice(0, 4000);
  const entry = { name, url: page.url(), title, shot: path.basename(shot), text_preview: text.slice(0, 500) };
  report.steps.push(entry);
  return entry;
}

await step('01-home', 'https://www.tiktok.com/');
report.logged_in = !/log in|sign up/i.test(report.steps[0].title) && !page.url().includes('/login');

await step('02-profile', PROFILE);
await step('03-studio', 'https://www.tiktok.com/tiktokstudio');
await step('04-studio-upload', 'https://www.tiktok.com/tiktokstudio/upload');
await step('05-activity', 'https://www.tiktok.com/activity');
await step('06-messages', 'https://www.tiktok.com/messages');
await step('07-notifications', 'https://www.tiktok.com/notification');

const allText = report.steps.map((s) => s.text_preview).join('\n');
const patterns = [
  /upload/i,
  /inbox/i,
  /draft/i,
  /complete.*(post|upload)/i,
  /pending/i,
  /open.*(app|tiktok)/i,
  /content posting/i,
  /finish.*(post|video)/i,
];
for (const re of patterns) {
  if (re.test(allText)) report.draft_hints.push(re.source);
}

fs.writeFileSync(path.join(OUT, 'tiktok-inbox-inspect.json'), JSON.stringify(report, null, 2));
console.log(
  JSON.stringify(
    {
      logged_in: report.logged_in,
      draft_hints: report.draft_hints,
      steps: report.steps.map((s) => ({ name: s.name, url: s.url, title: s.title })),
      out: OUT,
    },
    null,
    2,
  ),
);

await browser.close();
