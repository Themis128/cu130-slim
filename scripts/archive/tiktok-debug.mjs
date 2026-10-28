import { chromium } from "playwright";
import fs from "fs";

const browser = await chromium.launch({
  headless: true,
  executablePath: "/ms-playwright/chromium-1237/chrome-linux64/chrome",
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
const auth = JSON.parse(fs.readFileSync("/workspace/.tiktok-demo-auth.json", "utf-8"));
await context.addInitScript((t) => {
  localStorage.setItem("access_token", t.access_token);
  localStorage.setItem("refresh_token", t.refresh_token);
  localStorage.setItem("tour_completed", "true");
}, auth);
const page = await context.newPage();
await page.goto("http://social.cloudless.gr:8082/content/new", { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(5000);

// Click TikTok platform button
const tiktokBtn = page.locator('button:has-text("TikTok")').first();
const btnCount = await tiktokBtn.count();
console.log("TikTok button count:", btnCount);
if (btnCount > 0) {
  await tiktokBtn.click();
  await page.waitForTimeout(2000);
}

// Check for the publish mode selector
const selectExists = await page.locator("#tiktok-publish-mode").count();
console.log("Publish mode select exists:", selectExists);

// Dump all select elements
const selects = await page.evaluate(() => {
  return Array.from(document.querySelectorAll("select")).map(s => ({
    id: s.id,
    ariaLabel: s.getAttribute("aria-label"),
    options: Array.from(s.options).map(o => o.value),
  }));
});
console.log("All selects:", JSON.stringify(selects, null, 2));

// Check if TikTok platform is selected
const selectedPlatforms = await page.evaluate(() => {
  const btns = Array.from(document.querySelectorAll('button[data-platform]'));
  return btns.map(b => ({ platform: b.dataset.platform, classes: b.className.substring(0, 100) }));
});
console.log("Platform buttons:", JSON.stringify(selectedPlatforms, null, 2));

// Scroll down to see if the selector is below the fold
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await page.waitForTimeout(2000);
const selectAfterScroll = await page.locator("#tiktok-publish-mode").count();
console.log("Publish mode select after scroll:", selectAfterScroll);

await browser.close();
