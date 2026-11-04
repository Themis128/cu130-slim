// meta_secret.mjs — capture the Meta App Secret through the sidecar's
// stealth browser (patchright + stored FB session).
// CRITICAL: Meta's consent/continue prompts reject synthetic JS clicks —
// use playwright's trusted click() (native input) everywhere.
//
//   FB_PASSWORD=<pw> node /app/meta_secret.mjs [--app-id 1936126137016578]
import { chromium } from "patchright";
import fs from "fs";

const APP_ID = process.argv.find(a => a.startsWith("--app-id="))?.split("=")[1]
  || "1936126137016578";
const PASSWORD = process.env.FB_PASSWORD;
const SESSION_FILE = process.env.SESSION_FILE || "/data/fb-session.json";
if (!PASSWORD) { console.error("FB_PASSWORD env required"); process.exit(1); }

const state = JSON.parse(fs.readFileSync(SESSION_FILE, "utf-8"));
const browser = await chromium.launch({
  headless: true,
  args: ["--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-site-isolation-trials", "--no-sandbox",
    "--disable-dev-shm-usage", "--disable-gpu", "--disable-infobars",
    "--window-size=1920,1080"],
});
const ctx = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  locale: "en-US", timezoneId: "Europe/Athens",
  extraHTTPHeaders: { "Accept-Language": "en-US,en;q=0.9" },
  storageState: state.storageState,
});
await ctx.addInitScript(
  "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})");
const page = await ctx.newPage();

const trustedClick = async (re) => {
  const b = page.getByRole("button", { name: re }).or(
    page.getByRole("link", { name: re })).first();
  if (await b.count()) { await b.click({ timeout: 8000 }).catch(() => {}); return true; }
  return false;
};

try {
  await page.goto(
    `https://developers.facebook.com/apps/${APP_ID}/settings/basic/`,
    { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForTimeout(6000);

  let body = await page.evaluate(() => document.body.innerText);
  if (/Log into Meta for Developers|Continue with Facebook/i.test(body)) {
    await trustedClick(/Continue with Facebook/i);
    await page.waitForTimeout(7000);
  }
  // facebook.com SSO "Continue as <name>" — trusted click
  body = await page.evaluate(() => document.body.innerText);
  if (/^Continue$/m.test(body) || /Use another profile/i.test(body)) {
    await trustedClick(/^Continue$/i);
    await page.waitForTimeout(7000);
  }
  await page.goto(
    `https://developers.facebook.com/apps/${APP_ID}/settings/basic/`,
    { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForTimeout(6000);

  // click "Show" next to App Secret — trusted
  await trustedClick(/^Show$/i);
  await page.waitForTimeout(4000);

  // password re-auth dialog → fill + trusted confirm
  body = await page.evaluate(() => document.body.innerText);
  if (/password/i.test(body)) {
    const input = await page.$("input[type=password]");
    if (input) {
      await input.fill(PASSWORD);
      await page.waitForTimeout(800);
      await trustedClick(/confirm|continue|submit|ok/i);
      await page.waitForTimeout(5000);
    }
  }

  const secret = await page.evaluate(() => {
    const t = document.body.innerText;
    const i = t.indexOf("App Secret");
    if (i < 0) return "";
    const m = t.substring(i, i + 400).match(/\b([0-9a-f]{24,64})\b/i);
    return m ? m[1] : "";
  });
  if (!secret) {
    console.error("no secret visible — URL:", page.url());
    process.exit(1);
  }
  console.log(secret);
} finally {
  await browser.close();
}
