#!/usr/bin/env node
/**
 * TikTok Developer Portal demo video recorder.
 *
 * Records a complete end-to-end flow of the SocialAuto TikTok integration
 * at social.cloudless.gr showing:
 *   1. Login Kit — OAuth connection flow
 *   2. Content Posting API — upload draft (MEDIA_UPLOAD) mode
 *   3. Content Posting API — direct post (DIRECT_POST) mode with privacy levels
 *
 * Output: /workspace/docs/tiktok-demo/videos/*.webm → ffmpeg → tiktok-demo.mp4
 */
import { chromium } from "playwright";
import { existsSync, mkdirSync, readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const FRONTEND = "http://social.cloudless.gr:8082";
const BACKEND = "http://social.cloudless.gr:8083";
const OUTPUT_DIR = "/workspace/docs/tiktok-demo";
const VIDEO_DIR = join(OUTPUT_DIR, "videos");

if (!existsSync(VIDEO_DIR)) mkdirSync(VIDEO_DIR, { recursive: true });

// Read auth tokens from temp file
const authPath = "/tmp/tiktok-demo-auth.json";
let authTokens = null;
if (existsSync(authPath)) {
  authTokens = JSON.parse(readFileSync(authPath, "utf-8"));
}

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitForContent(page, selector, timeout = 15000) {
  try {
    await page.waitForSelector(selector, { state: "visible", timeout });
    return true;
  } catch {
    return false;
  }
}

async function run() {
  console.log("Launching Chromium with video recording...");
  const browser = await chromium.launch({
    headless: true,
    executablePath: "/ms-playwright/chromium-1237/chrome-linux64/chrome",
    args: [
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
      "--force-device-scale-factor=1",
    ],
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    recordVideo: {
      dir: VIDEO_DIR,
      size: { width: 1280, height: 720 },
    },
    locale: "en-US",
  });

  // Inject auth tokens before loading any page
  if (authTokens) {
    await context.addInitScript((tokens) => {
      localStorage.setItem("access_token", tokens.access_token);
      localStorage.setItem("refresh_token", tokens.refresh_token);
      localStorage.setItem("tour_completed", "true");
    }, authTokens);
  }

  const page = await context.newPage();

  // ─── Part 1: Landing page ────────────────────────────────────────────────
  console.log("[1/7] Loading SocialAuto at social.cloudless.gr...");
  await page.goto(FRONTEND, { waitUntil: "domcontentloaded", timeout: 30000 });
  // Wait for the SPA to hydrate — look for main content
  await waitForContent(page, "main, [role='main'], nav, .sidebar, h1");
  await sleep(3000);
  await page.screenshot({ path: join(OUTPUT_DIR, "01-homepage.png") });
  console.log("  Homepage loaded — domain visible in address bar");

  // ─── Part 2: Navigate to Accounts page ───────────────────────────────────
  console.log("[2/7] Navigating to Connected Accounts page...");
  await page.goto(`${FRONTEND}/accounts`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await waitForContent(page, "h1, main, [role='main']");
  await sleep(3000);
  await page.screenshot({ path: join(OUTPUT_DIR, "02-accounts.png") });
  console.log("  Accounts page loaded");

  // ─── Part 3: TikTok Login Kit — OAuth connection ─────────────────────────
  console.log("[3/7] Demonstrating TikTok Login Kit OAuth connection...");
  // Look for TikTok-related elements
  const tiktokElements = page.locator("text=/TikTok/i");
  const tiktokCount = await tiktokElements.count();
  console.log(`  Found ${tiktokCount} TikTok-related elements on accounts page`);

  if (tiktokCount > 0) {
    // Scroll to the TikTok section
    await tiktokElements.first().scrollIntoViewIfNeeded().catch(() => {});
    await sleep(2000);
    await page.screenshot({ path: join(OUTPUT_DIR, "03-tiktok-section.png") });

    // Look for a Connect button near TikTok
    const connectBtn = page.locator("button:has-text('Connect')").first();
    if (await connectBtn.count() > 0) {
      console.log("  Found Connect button — clicking to trigger TikTok OAuth...");
      await connectBtn.click({ timeout: 5000 }).catch(() => {});
      await sleep(3000);
      // The OAuth redirect will happen — screenshot the redirect state
      await page.screenshot({ path: join(OUTPUT_DIR, "04-tiktok-oauth-redirect.png") });
      console.log("  TikTok OAuth redirect triggered (Login Kit — user.info.basic scope)");
    }
  }

  // ─── Part 4: Content creation page ───────────────────────────────────────
  console.log("[4/7] Navigating to content creation page...");
  await page.goto(`${FRONTEND}/content/new`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await waitForContent(page, "h1, main, textarea, [role='main']");
  await sleep(3000);
  await page.screenshot({ path: join(OUTPUT_DIR, "05-content-new.png") });
  console.log("  Content creation page loaded");

  // ─── Part 5: Select TikTok platform ──────────────────────────────────────
  console.log("[5/7] Selecting TikTok platform...");
  // Find and click the TikTok platform toggle
  const tiktokBtn = page.locator("button:has-text('TikTok'), [data-platform='tiktok']").first();
  if (await tiktokBtn.count() > 0) {
    await tiktokBtn.click({ timeout: 5000 }).catch(() => {});
    await sleep(1500);
    console.log("  TikTok platform selected");
  } else {
    console.log("  TikTok button not found — trying text-based search");
    // Try clicking any element containing TikTok text in the platform selector area
    const platformBtn = page.locator("text=TikTok").first();
    if (await platformBtn.count() > 0) {
      await platformBtn.click({ timeout: 5000 }).catch(() => {});
      await sleep(1500);
    }
  }

  // Type content
  const textarea = page.locator("textarea").first();
  if (await textarea.count() > 0) {
    await textarea.click();
    await page.keyboard.type("Amazing cloud automation demo with TikTok integration! #cloudless #automation", { delay: 25 });
    await sleep(1000);
  }
  await page.screenshot({ path: join(OUTPUT_DIR, "06-tiktok-content.png") });

  // ─── Part 6: TikTok publishing options ───────────────────────────────────
  console.log("[6/7] Demonstrating TikTok publishing options...");

  // Show Upload Draft mode (MEDIA_UPLOAD — video.upload scope)
  const publishModeSelect = page.locator("#tiktok-publish-mode");
  if (await publishModeSelect.count() > 0) {
    await publishModeSelect.selectOption("MEDIA_UPLOAD");
    await sleep(2000);
    await publishModeSelect.scrollIntoViewIfNeeded().catch(() => {});
    await sleep(500);
    await page.screenshot({ path: join(OUTPUT_DIR, "07-tiktok-upload-draft.png") });
    console.log("  Upload Draft (MEDIA_UPLOAD) mode shown — video.upload scope");

    // Show Direct Post mode (DIRECT_POST — video.publish scope)
    await publishModeSelect.selectOption("DIRECT_POST");
    await sleep(2000);
    await page.screenshot({ path: join(OUTPUT_DIR, "08-tiktok-direct-post.png") });

    // Show privacy level selector
    const privacySelect = page.locator('[aria-label="TikTok privacy"]');
    if (await privacySelect.count() > 0) {
      console.log("  Demonstrating privacy level options (DIRECT_POST)...");
      await privacySelect.selectOption("SELF_ONLY");
      await sleep(1000);
      await page.screenshot({ path: join(OUTPUT_DIR, "09-privacy-self.png") });

      await privacySelect.selectOption("MUTUAL_FOLLOW_FRIENDS");
      await sleep(1000);
      await page.screenshot({ path: join(OUTPUT_DIR, "10-privacy-friends.png") });

      try {
        await privacySelect.selectOption("PUBLIC");
        await sleep(1000);
        await page.screenshot({ path: join(OUTPUT_DIR, "11-privacy-public.png") });
      } catch {
        // PUBLIC may not always be available
      }
    }
    console.log("  Direct Post (DIRECT_POST) mode with privacy levels shown — video.publish scope");
  } else {
    console.log("  TikTok publish mode selector not found — scrolling to find it");
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(1000);
    await page.screenshot({ path: join(OUTPUT_DIR, "07-tiktok-scroll.png") });
  }

  // ─── Part 7: Final overview ──────────────────────────────────────────────
  console.log("[7/7] Final overview — full page scroll...");
  await page.evaluate(() => window.scrollTo(0, 0));
  await sleep(1000);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(2000);
  await page.screenshot({ path: join(OUTPUT_DIR, "12-final-overview.png") });

  // Close the video recording
  console.log("Closing browser and finalizing video...");
  await page.close();
  await context.close();
  await browser.close();

  console.log(`\nVideo saved to: ${VIDEO_DIR}/`);
  console.log("Screenshots saved to: " + OUTPUT_DIR + "/");
  console.log("\nNext step: convert webm to mp4 with ffmpeg:");
  console.log(`  ffmpeg -i ${VIDEO_DIR}/*.webm -c:v libx264 -preset fast -crf 28 -vf scale=1280:720 /workspace/docs/tiktok-demo.mp4`);
}

run().catch((err) => {
  console.error("Demo video recording failed:", err);
  process.exit(1);
});
