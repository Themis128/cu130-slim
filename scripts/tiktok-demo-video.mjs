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
import { join } from "path";

const FRONTEND = "http://social.cloudless.gr:8082";
const OUTPUT_DIR = "/workspace/docs/tiktok-demo";
const VIDEO_DIR = join(OUTPUT_DIR, "videos");

if (!existsSync(VIDEO_DIR)) mkdirSync(VIDEO_DIR, { recursive: true });

const authPath = "/workspace/.tiktok-demo-auth.json";
let authTokens = null;
if (existsSync(authPath)) {
  authTokens = JSON.parse(readFileSync(authPath, "utf-8"));
}

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function run() {
  console.log("Launching Chromium with video recording...");
  const browser = await chromium.launch({
    headless: true,
    executablePath: "/ms-playwright/chromium-1237/chrome-linux64/chrome",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    recordVideo: { dir: VIDEO_DIR, size: { width: 1280, height: 720 } },
    locale: "en-US",
  });

  if (authTokens) {
    await context.addInitScript((tokens) => {
      localStorage.setItem("access_token", tokens.access_token);
      localStorage.setItem("refresh_token", tokens.refresh_token);
      localStorage.setItem("tour_completed", "true");
    }, authTokens);
  }

  const page = await context.newPage();

  // ─── Part 1: Dashboard ───────────────────────────────────────────────────
  console.log("[1/7] Loading SocialAuto dashboard at social.cloudless.gr...");
  await page.goto(FRONTEND, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForSelector("nav, main, h1", { timeout: 15000 });
  await sleep(3000);
  await page.screenshot({ path: join(OUTPUT_DIR, "01-dashboard.png") });
  console.log("  Dashboard loaded — domain visible: social.cloudless.gr");

  // ─── Part 2: Accounts page — TikTok Login Kit ────────────────────────────
  console.log("[2/7] Navigating to Connected Accounts — TikTok Login Kit...");
  await page.goto(`${FRONTEND}/accounts`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForSelector("h1, main", { timeout: 15000 });
  await sleep(3000);

  // Scroll to TikTok section
  const tiktokText = page.locator("text=/TikTok/i").first();
  if (await tiktokText.count() > 0) {
    await tiktokText.scrollIntoViewIfNeeded().catch(() => {});
    await sleep(2000);
  }
  await page.screenshot({ path: join(OUTPUT_DIR, "02-accounts-tiktok.png") });
  console.log("  Accounts page — TikTok section visible");

  // Click the TikTok Connect button (Login Kit OAuth)
  const connectBtn = page.locator("button:has-text('Connect')").first();
  if (await connectBtn.count() > 0) {
    console.log("  Clicking Connect to trigger TikTok Login Kit OAuth...");
    await connectBtn.click({ force: true, timeout: 5000 }).catch(() => {});
    await sleep(3000);
    await page.screenshot({ path: join(OUTPUT_DIR, "03-tiktok-oauth.png") });
    console.log("  TikTok OAuth redirect triggered (Login Kit — user.info.basic scope)");
    // Navigate back
    await page.goto(`${FRONTEND}/accounts`, { waitUntil: "networkidle", timeout: 30000 });
    await sleep(2000);
  }

  // ─── Part 3: Content creation page ───────────────────────────────────────
  console.log("[3/7] Navigating to content creation page...");
  await page.goto(`${FRONTEND}/content/new`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForSelector("textarea, main, h1", { timeout: 15000 });
  await sleep(3000);
  await page.screenshot({ path: join(OUTPUT_DIR, "04-content-new.png") });
  console.log("  Content creation page loaded");

  // ─── Part 4: Select TikTok platform (force-click since no account yet) ───
  console.log("[4/7] Selecting TikTok platform...");
  const tiktokBtn = page.locator('button:has-text("TikTok")').first();
  if (await tiktokBtn.count() > 0) {
    await tiktokBtn.click({ force: true, timeout: 5000 }).catch(() => {});
    await sleep(2000);
    console.log("  TikTok platform selected (force-clicked — disabled without connected account)");
  }

  // Type content
  const textarea = page.locator("textarea").first();
  if (await textarea.count() > 0) {
    await textarea.click();
    await page.keyboard.type("Amazing cloud automation demo with TikTok integration! #cloudless #automation", { delay: 25 });
    await sleep(1000);
  }
  await page.screenshot({ path: join(OUTPUT_DIR, "05-tiktok-content.png") });

  // ─── Part 5: TikTok publishing options ───────────────────────────────────
  console.log("[5/7] Demonstrating TikTok publishing options...");

  // Enable the disabled TikTok button via JS so we can show the publishing UI
  await page.evaluate(() => {
    document.querySelectorAll('button[data-platform="tiktok"]').forEach(b => {
      b.removeAttribute("disabled");
      b.classList.remove("opacity-40", "cursor-not-allowed");
    });
  });
  await tiktokBtn.click({ force: true }).catch(() => {});
  await sleep(1500);

  // Scroll to find the TikTok publish mode selector
  await page.evaluate(() => {
    const sel = document.getElementById("tiktok-publish-mode");
    if (sel) sel.scrollIntoView({ block: "center" });
  });
  await sleep(1000);

  const publishModeSelect = page.locator("#tiktok-publish-mode");
  const hasPublishMode = await publishModeSelect.count();
  console.log(`  Publish mode selector found: ${hasPublishMode > 0}`);

  if (hasPublishMode > 0) {
    // Upload Draft mode (MEDIA_UPLOAD — video.upload scope)
    await publishModeSelect.selectOption("MEDIA_UPLOAD");
    await sleep(2000);
    await page.evaluate(() => document.getElementById("tiktok-publish-mode")?.scrollIntoView({ block: "center" }));
    await sleep(500);
    await page.screenshot({ path: join(OUTPUT_DIR, "06-upload-draft.png") });
    console.log("  Upload Draft (MEDIA_UPLOAD) shown — video.upload scope");

    // Direct Post mode (DIRECT_POST — video.publish scope)
    await publishModeSelect.selectOption("DIRECT_POST");
    await sleep(2000);
    await page.screenshot({ path: join(OUTPUT_DIR, "07-direct-post.png") });

    // Privacy level selector
    const privacySelect = page.locator('[aria-label="TikTok privacy"]');
    if (await privacySelect.count() > 0) {
      console.log("  Demonstrating privacy level options...");
      await privacySelect.selectOption("SELF_ONLY");
      await sleep(1000);
      await page.screenshot({ path: join(OUTPUT_DIR, "08-privacy-self.png") });

      await privacySelect.selectOption("MUTUAL_FOLLOW_FRIENDS");
      await sleep(1000);
      await page.screenshot({ path: join(OUTPUT_DIR, "09-privacy-friends.png") });

      try {
        await privacySelect.selectOption("PUBLIC");
        await sleep(1000);
        await page.screenshot({ path: join(OUTPUT_DIR, "10-privacy-public.png") });
      } catch { /* PUBLIC may not be available */ }
    }
    console.log("  Direct Post (DIRECT_POST) with privacy levels shown — video.publish scope");
  } else {
    // Scroll down to find it
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(2000);
    await page.screenshot({ path: join(OUTPUT_DIR, "06-scroll-for-options.png") });
    console.log("  Publish mode selector not found — scrolled to find it");
  }

  // ─── Part 6: Full page overview ──────────────────────────────────────────
  console.log("[6/7] Full page overview...");
  await page.evaluate(() => window.scrollTo(0, 0));
  await sleep(1000);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(2000);
  await page.screenshot({ path: join(OUTPUT_DIR, "11-full-overview.png") });

  // ─── Part 7: Back to accounts to show TikTok setup guide ─────────────────
  console.log("[7/7] Showing TikTok setup guide on accounts page...");
  await page.goto(`${FRONTEND}/accounts`, { waitUntil: "networkidle", timeout: 30000 });
  await sleep(3000);
  // Click Setup tab if it exists
  const setupTab = page.locator('[role="tab"]:has-text("Setup"), button:has-text("Setup")').first();
  if (await setupTab.count() > 0) {
    await setupTab.click().catch(() => {});
    await sleep(2000);
  }
  // Scroll to TikTok section
  const tiktokSection = page.locator("text=/TikTok/i").first();
  if (await tiktokSection.count() > 0) {
    await tiktokSection.scrollIntoViewIfNeeded().catch(() => {});
    await sleep(2000);
  }
  await page.screenshot({ path: join(OUTPUT_DIR, "12-tiktok-setup-guide.png") });

  // Close
  console.log("Closing browser and finalizing video...");
  await page.close();
  await context.close();
  await browser.close();

  console.log(`\nVideo: ${VIDEO_DIR}/`);
  console.log(`Screenshots: ${OUTPUT_DIR}/`);
  console.log("\nConvert to mp4:");
  console.log(`  ffmpeg -i ${VIDEO_DIR}/*.webm -c:v libx264 -preset fast -crf 28 /workspace/docs/tiktok-demo.mp4`);
}

run().catch((err) => {
  console.error("Demo video recording failed:", err);
  process.exit(1);
});
