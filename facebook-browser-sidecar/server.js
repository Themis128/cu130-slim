/**
 * Facebook Browser Automation Sidecar
 *
 * REST API that drives a persistent Playwright browser session for Facebook.
 * Covers personal-profile operations that the official Graph API does NOT
 * support (personal posting was removed from the Graph API years ago):
 *
 * Personal profile endpoints:
 *   GET  /health
 *   POST /session              — set storage state (cookies + localStorage)
 *   GET  /session              — check if session is alive
 *   GET  /profile              — read personal profile (name, bio, avatar, cover)
 *   POST /profile/bio          — update bio / intro text
 *   POST /profile/picture      — upload profile picture
 *   POST /profile/cover        — upload cover photo
 *   POST /profile/website      — update website in contact info
 *
 * Personal posting endpoints (the main gap the Graph API leaves):
 *   POST /post/text            — post a text status update
 *   POST /post/photo           — post a photo (single or multi) with caption
 *   POST /post/link            — post a link with optional message
 *   POST /post/video           — upload a video with caption
 *
 * Page mode (optional — switch to a managed Page for posting):
 *   GET  /pages                — list Pages the user manages
 *   POST /page/:page_id/use    — switch the active session to a Page
 *   POST /page/post/text       — post text as the current Page
 *   POST /page/post/photo      — post a photo as the current Page
 *
 * Session is injected via POST /session with a Playwright storage_state
 * JSON (cookies + origins).  The browser-novnc container or the
 * BrowserProfileService.login_facebook() flow produces this state.
 */

import express from 'express';
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import os from 'os';

const PORT = process.env.FACEBOOK_SIDECAR_PORT || 9226;

// ── Browser lifecycle ─────────────────────────────────────────────────────

let browser = null;
let context = null;
let page = null;
let storageState = null;
let activePageId = null; // set when switched to a Page profile

async function ensureBrowser() {
  if (browser && browser.isConnected()) return;

  // Try to load a saved session if we don't have one in memory
  if (!storageState) {
    await loadSavedSession();
  }

  browser = await chromium.launch({
    headless: true,
    args: [
      '--disable-blink-features=AutomationControlled',
      '--disable-features=IsolateOrigins,site-per-process',
      '--disable-site-isolation-trials',
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--disable-infobars',
      '--window-size=1920,1080',
    ],
  });

  const ctxOptions = {
    viewport: { width: 1920, height: 1080 },
    userAgent:
      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    locale: 'en-US',
    timezoneId: 'Europe/Athens',
    extraHTTPHeaders: { 'Accept-Language': 'en-US,en;q=0.9' },
    permissions: ['notifications'],
  };

  if (storageState) {
    ctxOptions.storageState = storageState;
  }

  context = await browser.newContext(ctxOptions);
  await context.addInitScript(
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
  );
  page = await context.newPage();
}

async function closeBrowser() {
  if (browser) {
    await browser.close().catch(() => {});
    browser = null;
    context = null;
    page = null;
  }
}

/** Best-effort wait for page to settle (Facebook has long-polling). */
async function settle(timeout = 20000) {
  try {
    await page.waitForLoadState('networkidle', { timeout });
  } catch (_) {}
  await page.waitForTimeout(1500);
}

/** Save a Buffer to a temp file and return the path. */
function bufferToTempFile(buffer, filename) {
  const ext = path.extname(filename) || '.jpg';
  const tmp = path.join(os.tmpdir(), `fb-sidecar-${Date.now()}${ext}`);
  fs.writeFileSync(tmp, buffer);
  return tmp;
}

/** Check if we're logged in by looking at the current URL. */
function isLoggedIn() {
  const url = page.url();
  return (
    url.includes('facebook.com') &&
    !url.includes('/login') &&
    !url.includes('/checkpoint') &&
    !url.includes('/recover')
  );
}

/** Find and click a Save button in a dialog or page. */
async function clickSave() {
  for (const sel of [
    'div[role="dialog"] button:has-text("Save")',
    'button[type="submit"]:has-text("Save")',
    'button:has-text("Save")',
    'div[role="dialog"] [role="button"]:has-text("Save")',
  ]) {
    const btn = page.locator(sel).first();
    if (await btn.isVisible().catch(() => false)) {
      await btn.click();
      await settle();
      return true;
    }
  }
  return false;
}

/** Dismiss any modal dialog by pressing Escape. */
async function closeDialogs() {
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
  }
}

// ── Robust selector framework ──────────────────────────────────────────────

/**
 * Try multiple selector strategies in order and return the first visible element.
 * Each strategy is a Playwright selector string. This makes the sidecar
 * resilient to Facebook layout changes.
 *
 * @param {string[]} selectors - Ordered list of CSS/Playwright selectors to try
 * @param {object} opts - { timeout: ms per selector, visible: require visibility }
 * @returns {Promise<Locator|null>} - The first matching visible locator, or null
 */
async function findElement(selectors, opts = {}) {
  const { timeout = 3000, visible = true } = opts;
  for (const sel of selectors) {
    try {
      const loc = page.locator(sel).first();
      if (visible) {
        if (await loc.isVisible({ timeout }).catch(() => false)) {
          return loc;
        }
      } else {
        if ((await loc.count()) > 0) {
          return loc;
        }
      }
    } catch (_) {}
  }
  return null;
}

/**
 * Find and click the first visible element from a list of selectors.
 * Includes retry logic — tries each selector, then waits and retries up to
 * `retries` times.
 */
async function findAndClick(selectors, opts = {}) {
  const { retries = 2, settleMs = 1500 } = opts;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const el = await findElement(selectors, { timeout: 3000 });
    if (el) {
      await el.click().catch(() => {});
      await page.waitForTimeout(settleMs);
      return true;
    }
    if (attempt < retries) await page.waitForTimeout(2000);
  }
  return false;
}

/**
 * Find a textarea or contenteditable input inside a dialog or page.
 * Tries multiple strategies: aria-label, placeholder, dialog-scoped, any visible.
 */
async function findTextInput(opts = {}) {
  const { scope = 'any', label = null } = opts;
  const dialogScope = scope === 'dialog' ? 'div[role="dialog"] ' : '';
  const strategies = [];
  if (label) {
    strategies.push(
      `${dialogScope}textarea[aria-label*="${label}"]:visible`,
      `${dialogScope}textarea[placeholder*="${label}"]:visible`,
      `${dialogScope}input[aria-label*="${label}"]:visible`,
      `${dialogScope}input[placeholder*="${label}"]:visible`,
    );
  }
  strategies.push(
    `${dialogScope}textarea:visible`,
    `${dialogScope}[contenteditable="true"]:visible`,
    `${dialogScope}input[type="text"]:visible`,
  );
  return findElement(strategies, { timeout: 2000 });
}

/**
 * Navigate to a URL, settle, dismiss dialogs, and verify login.
 * Returns true if logged in, false otherwise.
 */
async function navigateAndCheck(url) {
  await ensureBrowser();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await settle();
  await closeDialogs();
  return isLoggedIn();
}

/**
 * Extract text from the page body, split into trimmed lines.
 * Useful for finding elements by text context when selectors fail.
 */
async function getPageLines() {
  const text = await page.innerText('body').catch(() => '');
  return text.split('\n').map(l => l.trim()).filter(l => l);
}

/**
 * Find a clickable element near a text label by scanning the DOM tree.
 * Returns the locator or null.
 */
async function findButtonNearText(text, maxLevels = 5) {
  return page.evaluate(({ text, maxLevels }) => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.textContent && node.textContent.trim() === text) {
        let el = node.parentElement;
        for (let i = 0; i < maxLevels && el; i++) {
          const btn = el.querySelector('[role="button"], button, a[href]');
          if (btn && btn.offsetParent !== null) {
            // Return a CSS path to this element
            return el.getAttribute('data-testid') ||
              `[role="button"]:has-text("${text}")`;
          }
          el = el.parentElement;
        }
      }
    }
    return null;
  }, { text, maxLevels });
}

// ── Session persistence ────────────────────────────────────────────────────

const SESSION_FILE = process.env.SESSION_FILE || '/data/fb-session.json';

async function saveSession() {
  try {
    if (!context) return;
    const state = await context.storageState();
    const cookies = await context.cookies();
    fs.writeFileSync(SESSION_FILE, JSON.stringify({ storageState: state, cookies, savedAt: Date.now() }));
  } catch (_) {}
}

async function loadSavedSession() {
  try {
    if (!fs.existsSync(SESSION_FILE)) return false;
    const data = JSON.parse(fs.readFileSync(SESSION_FILE, 'utf-8'));
    if (data.storageState) {
      storageState = data.storageState;
      return true;
    }
  } catch (_) {}
  return false;
}

async function exportCookies() {
  if (!context) return {};
  const cookies = await context.cookies();
  const result = {};
  for (const c of cookies) {
    if (c.domain && c.domain.includes('facebook.com')) {
      result[c.name] = c.value;
    }
  }
  return result;
}

// ── API: Session ───────────────────────────────────────────────────────────

async function handleSetSession(req, res) {
  const { storage_state, cookies } = req.body;
  if (!storage_state && !cookies) {
    return res.status(400).json({ error: 'storage_state or cookies is required' });
  }
  // If cookies dict is provided, build a storage_state from it
  if (cookies && !storage_state) {
    const cookieList = Object.entries(cookies).map(([name, value]) => ({
      name, value, domain: '.facebook.com', path: '/',
    }));
    storageState = { cookies: cookieList, origins: [] };
  } else {
    storageState = storage_state;
  }
  activePageId = null;
  await closeBrowser();
  await ensureBrowser();
  try {
    await page.goto('https://www.facebook.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();
    const loggedIn = isLoggedIn();
    if (loggedIn) await saveSession();
    res.json({ status: 'ok', logged_in: loggedIn, url: page.url() });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

async function handleCheckSession(req, res) {
  try {
    await ensureBrowser();
    await page.goto('https://www.facebook.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();
    const loggedIn = isLoggedIn();
    res.json({ status: 'ok', logged_in: loggedIn, url: page.url(), active_page_id: activePageId });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Login (username + password) ───────────────────────────────────────

async function handleLogin(req, res) {
  const { username, password, verification_code } = req.body;
  if (!username || !password) {
    return res.status(400).json({ error: 'username and password are required' });
  }
  try {
    await ensureBrowser();
    await page.goto('https://www.facebook.com/login', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForSelector('input[name="email"]', { timeout: 30000 });

    await page.fill('input[name="email"]', username);
    await page.fill('input[name="pass"]', password);
    await page.press('input[name="pass"]', 'Enter');

    // Wait for navigation
    await page.waitForTimeout(8000);

    const currentUrl = page.url();

    // Check for 2FA / checkpoint
    if (currentUrl.includes('checkpoint') || currentUrl.includes('two_factor')) {
      if (verification_code) {
        const codeInput = page.locator('input#approvals_code, input[name="approvals_code"]').first();
        if (await codeInput.count() > 0) {
          await codeInput.fill(verification_code);
          await codeInput.press('Enter');
          await page.waitForTimeout(5000);
          if (isLoggedIn()) {
            storageState = await context.storageState();
            await saveSession();
            res.json({ status: 'ok', logged_in: true, storage_state: storageState });
          } else {
            res.json({ status: 'ok', logged_in: false, two_factor_required: true, message: '2FA code rejected' });
          }
        } else {
          res.json({ status: 'ok', logged_in: false, two_factor_required: true, message: '2FA required but no code input found' });
        }
      } else {
        res.json({ status: 'ok', logged_in: false, two_factor_required: true, message: 'Facebook 2FA required. Provide verification_code and retry.' });
      }
      return;
    }

    // Check if login succeeded
    if (isLoggedIn()) {
      storageState = await context.storageState();
      await saveSession();
      res.json({ status: 'ok', logged_in: true, storage_state: storageState });
    } else {
      // Check for error message
      let errorMsg = 'Facebook login failed';
      try {
        const errorEl = page.locator("div[role='alert'], .login_error, [data-testid='royal_login_error']").first();
        if (await errorEl.count() > 0) {
          errorMsg = await errorEl.innerText();
        }
      } catch (_) {}
      res.json({ status: 'ok', logged_in: false, message: errorMsg, url: currentUrl });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal profile read ─────────────────────────────────────────────

async function handleReadProfile(req, res) {
  try {
    await ensureBrowser();

    // Navigate to the user's own profile — Facebook redirects /me to the correct profile URL
    await page.goto('https://www.facebook.com/me', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // If redirected to home, try profile.php with the user ID from the page
    let profileUrl = page.url();
    if (profileUrl === 'https://www.facebook.com/' || profileUrl.endsWith('facebook.com/')) {
      // Try extracting user ID from cookies or page source
      const userId = await page.evaluate(() => {
        // Try to get user ID from the page's data
        const el = document.querySelector('[data-userid]') || document.querySelector('[id^="pagelet_timeline"]');
        if (el) return el.getAttribute('data-userid');
        // Try from the JSON data
        const scripts = document.querySelectorAll('script[type="application/json"]');
        for (const s of scripts) {
          const text = s.textContent || '';
          const match = text.match(/"user_id":"(\d+)"/);
          if (match) return match[1];
        }
        return null;
      }).catch(() => null);

      if (userId) {
        await page.goto(`https://www.facebook.com/${userId}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
        await settle();
        profileUrl = page.url();
      } else {
        // Last resort: try the profile link in the navigation
        const profileLink = page.locator('a[aria-label*="Profile"], a[aria-label*="profile"], a[href*="/profile.php?id="]').first();
        if (await profileLink.count() > 0) {
          const href = await profileLink.getAttribute('href');
          if (href) {
            await page.goto(href.startsWith('http') ? href : `https://www.facebook.com${href}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
            await settle();
            profileUrl = page.url();
          }
        }
      }
    }

    const result = {
      url: profileUrl,
      name: null,
      bio: null,
      profile_pic_url: null,
      cover_url: null,
      friends_count: null,
    };

    // Name — Facebook shows the user's name in the profile header
    // Avoid picking up "Notifications" or other nav bar h1 elements
    const nameEl = page.locator('[data-pagelet="ProfileName"] h1, h1[data-test-id="profile-name"], section h1, [role="main"] h1').first();
    if (await nameEl.count() > 0) {
      result.name = (await nameEl.innerText()).trim();
    }
    if (!result.name || result.name === 'Notifications') {
      // Fallback: get name from the page title (e.g. "Themistoklis Baltzakis | Facebook")
      const titleName = await page.title().catch(() => '');
      if (titleName && titleName.includes('|')) {
        result.name = titleName.split('|')[0].trim();
      } else if (titleName && !titleName.includes('Facebook') && !titleName.includes('Notifications')) {
        result.name = titleName.trim();
      }
    }
    if (!result.name || result.name === 'Notifications') {
      // Try the og:title meta tag
      const ogTitle = await page.evaluate(() => {
        const meta = document.querySelector('meta[property="og:title"]');
        return meta ? meta.getAttribute('content') : null;
      }).catch(() => null);
      if (ogTitle) result.name = ogTitle;
    }
    if (!result.name || result.name === 'Notifications') {
      // Last resort: extract from page text — look for a name-like string after the notifications count
      const pageText = await page.innerText('body').catch(() => '');
      const lines = pageText.split('\n').map(l => l.trim()).filter(l => l);
      // The name usually appears right after "Edit cover photo" on the profile page
      const editCoverIdx = lines.findIndex(l => l.toLowerCase().includes('edit cover photo'));
      if (editCoverIdx >= 0 && editCoverIdx + 1 < lines.length) {
        const candidate = lines[editCoverIdx + 1];
        if (candidate && !candidate.includes('notifications') && !candidate.includes('Notifications') && candidate.length > 2) {
          result.name = candidate;
        }
      }
    }

    // Profile picture
    const profilePic = page.locator('img[data-imgperflogname="profilePhoto"], img[alt*="Profile photo"], img[alt*="profile photo"], img[src*="profile_pic"]').first();
    if (await profilePic.count() > 0) {
      result.profile_pic_url = await profilePic.getAttribute('src');
    }

    // Cover photo
    const coverImg = page.locator('img[data-imgperflogname="coverPhoto"], img[alt*="Cover"], img[alt*="cover"], img[src*="cover"]').first();
    if (await coverImg.count() > 0) {
      result.cover_url = await coverImg.getAttribute('src');
    }

    // Bio / intro — try the about page for reliability
    try {
      await page.goto(profileUrl + '?sk=about', { waitUntil: 'domcontentloaded', timeout: 30000 });
      await settle();
      const bioEl = page.locator('[data-pagelet="ProfileActions"] span, div[data-sigil*="intro"] span, span:has-text("Lives in"), div[class*="intro"] span').first();
      if (await bioEl.count() > 0) {
        result.bio = (await bioEl.innerText()).trim();
      }
    } catch (_) {}

    res.json({ status: 'ok', profile: result });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal bio update ───────────────────────────────────────────────

async function handleUpdateBio(req, res) {
  const { bio } = req.body;
  if (bio === undefined || bio === null) {
    return res.status(400).json({ error: 'bio is required' });
  }
  try {
    await ensureBrowser();
    // Navigate to the about page where the "About you" / Bio edit control is
    await page.goto('https://www.facebook.com/profile.php?sk=about', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Click the "About you" button (the bio edit control on the about page)
    // Facebook shows it as a div with role="button" containing "About you" text
    const aboutYouBtn = page.locator('[role="button"]:has-text("About you"), [role="button"]:has-text("Edit details"), [role="button"]:has-text("Add bio"), [role="button"]:has-text("Edit bio")').first();
    if (await aboutYouBtn.count() === 0) {
      return res.status(404).json({ error: 'Could not find the "About you" / bio edit button on the about page' });
    }
    await aboutYouBtn.click();
    await page.waitForTimeout(3000);

    // A dialog should open with a textarea for the bio
    let bioInput = page.locator('div[role="dialog"] textarea').first();
    if (await bioInput.count() === 0) {
      bioInput = page.locator('textarea[aria-label*="Bio"], textarea[aria-label*="bio"], textarea[aria-label*="intro"], textarea[placeholder*="bio"], textarea[placeholder*="Bio"]').first();
    }
    if (await bioInput.count() === 0) {
      // Try contenteditable div
      bioInput = page.locator('div[role="dialog"] [contenteditable="true"]').first();
    }
    if (await bioInput.count() === 0) {
      // Debug: list what's in the dialog
      const dialogText = await page.locator('div[role="dialog"]').innerText().catch(() => 'no dialog');
      return res.status(404).json({ error: 'Could not locate the bio textarea in the edit dialog', dialogText: dialogText.substring(0, 300) });
    }

    await bioInput.click();
    await page.keyboard.press('Control+a');
    await page.keyboard.press('Delete');
    await page.keyboard.type(bio);
    await page.waitForTimeout(500);

    // Save — look for a Save button in the dialog
    const saved = await clickSave();
    if (!saved) {
      // Try pressing Enter as fallback
      await page.keyboard.press('Enter');
      await settle();
    }

    res.json({ status: 'ok', updated: ['bio'] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal profile picture ──────────────────────────────────────────

async function handleUploadPicture(req, res) {
  const { image_base64, filename } = req.body;
  if (!image_base64) return res.status(400).json({ error: 'image_base64 is required' });
  try {
    await ensureBrowser();
    await page.goto('https://www.facebook.com/profile.php', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Click the "Update profile picture" / camera icon on the profile photo
    const picEdit = page.locator(
      '[aria-label="Update profile picture"], [aria-label="Change profile photo"], ' +
      '[aria-label*="profile photo"], [aria-label*="Profile photo"], ' +
      'a:has-text("Update profile picture"), [role="button"]:has-text("Update profile picture"), ' +
      '[role="button"]:has-text("Add Photo")'
    ).first();

    // Hover over the profile picture area to reveal the edit button
    if (await picEdit.count() === 0) {
      const picArea = page.locator('img[data-imgperflogname="profilePhoto"], img[alt*="Profile"]').first();
      if (await picArea.count() > 0) {
        await picArea.hover();
        await page.waitForTimeout(1000);
      }
    }

    if (await picEdit.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the profile photo edit button' });
    }
    await picEdit.click();
    await page.waitForTimeout(2000);

    // Click "Upload Photo" if a submenu appears
    const uploadOption = page.locator('[role="button"]:has-text("Upload Photo"), [role="menuitem"]:has-text("Upload Photo"), span:has-text("Upload Photo")').first();
    if (await uploadOption.isVisible().catch(() => false)) {
      await uploadOption.click();
      await page.waitForTimeout(1000);
    }

    const buffer = Buffer.from(image_base64, 'base64');
    const tmpPath = bufferToTempFile(buffer, filename || 'profile.jpg');

    try {
      const fileInput = page.locator('input[type="file"][accept*="image"]').first();
      await fileInput.setInputFiles(tmpPath);
      await page.waitForTimeout(3000);

      // Handle crop/adjust step — click "Save" or "Done"
      for (const btnText of ['Save', 'Done', 'Apply', 'Create']) {
        const btn = page.locator(`div[role="dialog"] button:has-text("${btnText}"), button:has-text("${btnText}"), [role="button"]:has-text("${btnText}")`).first();
        if (await btn.isVisible().catch(() => false)) {
          await btn.click();
          await page.waitForTimeout(2000);
          break;
        }
      }

      // Final save if a second step exists
      await clickSave();
      res.json({ status: 'ok', updated: ['profile_picture'] });
    } finally {
      fs.unlinkSync(tmpPath);
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal cover photo ──────────────────────────────────────────────

async function handleUploadCover(req, res) {
  const { image_base64, filename } = req.body;
  if (!image_base64) return res.status(400).json({ error: 'image_base64 is required' });
  try {
    await ensureBrowser();
    await page.goto('https://www.facebook.com/profile.php', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Click "Edit Cover Photo" / camera icon on the cover area
    const coverEdit = page.locator(
      '[aria-label="Edit Cover Photo"], [aria-label*="cover photo"], [aria-label*="Cover Photo"], ' +
      'a:has-text("Edit Cover Photo"), [role="button"]:has-text("Edit Cover Photo"), ' +
      '[role="button"]:has-text("Add Cover Photo")'
    ).first();

    // Hover over the cover area to reveal the edit button
    if (await coverEdit.count() === 0) {
      const coverArea = page.locator('img[data-imgperflogname="coverPhoto"], [class*="cover"], img[alt*="Cover"]').first();
      if (await coverArea.count() > 0) {
        await coverArea.hover();
        await page.waitForTimeout(1000);
      }
    }

    if (await coverEdit.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the cover photo edit button' });
    }
    await coverEdit.click();
    await page.waitForTimeout(2000);

    // Click "Upload Photo" if a submenu appears
    const uploadOption = page.locator('[role="button"]:has-text("Upload Photo"), [role="menuitem"]:has-text("Upload Photo"), span:has-text("Upload Photo")').first();
    if (await uploadOption.isVisible().catch(() => false)) {
      await uploadOption.click();
      await page.waitForTimeout(1000);
    }

    const buffer = Buffer.from(image_base64, 'base64');
    const tmpPath = bufferToTempFile(buffer, filename || 'cover.jpg');

    try {
      const fileInput = page.locator('input[type="file"][accept*="image"]').first();
      await fileInput.setInputFiles(tmpPath);
      await page.waitForTimeout(3000);

      // Handle repositioning step — click "Save" or "Done"
      for (const btnText of ['Save', 'Done', 'Apply']) {
        const btn = page.locator(`div[role="dialog"] button:has-text("${btnText}"), button:has-text("${btnText}"), [role="button"]:has-text("${btnText}")`).first();
        if (await btn.isVisible().catch(() => false)) {
          await btn.click();
          await page.waitForTimeout(2000);
          break;
        }
      }

      res.json({ status: 'ok', updated: ['cover'] });
    } finally {
      fs.unlinkSync(tmpPath);
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal website (contact info) ───────────────────────────────────

async function handleUpdateWebsite(req, res) {
  const { website } = req.body;
  if (!website) return res.status(400).json({ error: 'website is required' });
  try {
    await ensureBrowser();
    await page.goto('https://www.facebook.com/profile.php?sk=about_contact', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Click "Edit" or "Add a website" in the contact info section
    const editBtn = page.locator('[role="button"]:has-text("Edit"), [aria-label*="Edit"], span:has-text("Edit"), a:has-text("Edit Details"), [role="button"]:has-text("Edit Details")').first();
    if (await editBtn.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the contact info "Edit" button' });
    }
    await editBtn.click();
    await page.waitForTimeout(2000);

    // Find the website input — Facebook uses a text input with a placeholder
    // like "Website" or an aria-label containing "website"
    let websiteInput = page.locator('input[aria-label*="Website"], input[aria-label*="website"], input[placeholder*="Website"], input[placeholder*="website"]').first();
    if (await websiteInput.count() === 0) {
      // Fallback: look for any input in the dialog that's not the name
      const inputs = await page.locator('div[role="dialog"] input:visible, form input:visible').all();
      for (const inp of inputs) {
        const ph = await inp.getAttribute('placeholder').catch(() => '');
        const label = await inp.getAttribute('aria-label').catch(() => '');
        if (
          (ph && (ph.includes('http') || ph.includes('url') || ph.includes('Website') || ph.includes('website'))) ||
          (label && (label.includes('Website') || label.includes('website')))
        ) {
          websiteInput = inp;
          break;
        }
      }
    }
    if (await websiteInput.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the website input in contact info' });
    }

    await websiteInput.click();
    await page.keyboard.press('Control+a');
    await page.keyboard.press('Delete');
    await page.keyboard.type(website);
    await page.waitForTimeout(500);

    await clickSave();
    res.json({ status: 'ok', updated: ['website'] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal posting — text status ────────────────────────────────────

async function handlePostText(req, res) {
  const { message, privacy } = req.body;
  if (!message) return res.status(400).json({ error: 'message is required' });
  // privacy: 'public' | 'friends' | 'only_me' (default: friends)
  try {
    await ensureBrowser();
    await page.goto('https://www.facebook.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Click the "What's on your mind?" composer
    const composer = page.locator(
      '[role="button"]:has-text("What\'s on your mind"), ' +
      'div:has-text("What\'s on your mind"):not(:has(div:has-text("What\'s on your mind")))'
    ).first();
    // Fallback: the composer trigger is often a label/placeholder
    let composerTrigger = composer;
    if (await composerTrigger.count() === 0) {
      composerTrigger = page.locator('[role="button"][aria-label*="on your mind"], [aria-label*="Create a post"]').first();
    }
    if (await composerTrigger.count() === 0) {
      // Fallback: click the text input area at the top of the feed
      composerTrigger = page.locator('div[role="textbox"]:visible').first();
    }
    if (await composerTrigger.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the Facebook post composer' });
    }
    await composerTrigger.click();
    await page.waitForTimeout(2000);

    // Set privacy if specified (before typing)
    if (privacy) {
      await setPrivacy(privacy);
    }

    // Type the message into the contenteditable composer
    const editor = page.locator('div[role="textbox"]:visible, div[contenteditable="true"]:visible').first();
    if (await editor.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the post text editor' });
    }
    await editor.click();
    await page.waitForTimeout(500);
    await page.keyboard.type(message);
    await page.waitForTimeout(1000);

    // Click "Post"
    const posted = await clickPost();
    if (!posted) {
      return res.status(500).json({ error: 'Could not find the Post button after typing' });
    }

    await settle();
    res.json({ status: 'ok', posted: true, message: 'Text status posted to personal profile' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal posting — photo(s) with caption ──────────────────────────

async function handlePostPhoto(req, res) {
  const { message, images, privacy } = req.body;
  // images: array of { image_base64, filename } — 1 to 10 photos
  if (!images || !Array.isArray(images) || images.length === 0) {
    return res.status(400).json({ error: 'images must be a non-empty array of { image_base64, filename }' });
  }
  if (images.length > 10) {
    return res.status(400).json({ error: 'Maximum 10 photos per post' });
  }
  try {
    await ensureBrowser();
    await page.goto('https://www.facebook.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Click "Photo/video" under the composer, or open the composer first
    const photoVideoBtn = page.locator('[role="button"]:has-text("Photo/video"), [role="button"]:has-text("Photo/Video"), a:has-text("Photo/video")').first();
    if (await photoVideoBtn.count() > 0) {
      await photoVideoBtn.click();
      await page.waitForTimeout(2000);
    } else {
      // Open the composer first, then find the photo/video tab
      const composer = page.locator('[role="button"]:has-text("What\'s on your mind"), [aria-label*="on your mind"]').first();
      if (await composer.count() > 0) {
        await composer.click();
        await page.waitForTimeout(2000);
        const tab = page.locator('[role="button"]:has-text("Photo/video"), [role="button"]:has-text("Photo/Video")').first();
        if (await tab.count() > 0) {
          await tab.click();
          await page.waitForTimeout(1000);
        }
      }
    }

    // Set privacy if specified
    if (privacy) {
      await setPrivacy(privacy);
    }

    // Upload all images via the file input
    const fileInput = page.locator('input[type="file"][accept*="image"]').first();
    if (await fileInput.count() === 0) {
      // Wait a bit more for the input to appear after clicking Photo/video
      await page.waitForTimeout(2000);
    }
    if (await fileInput.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the photo file input' });
    }

    const tmpPaths = [];
    try {
      for (const img of images) {
        const buffer = Buffer.from(img.image_base64, 'base64');
        const tmpPath = bufferToTempFile(buffer, img.filename || `photo-${Date.now()}.jpg`);
        tmpPaths.push(tmpPath);
      }
      await fileInput.setInputFiles(tmpPaths);
      await page.waitForTimeout(3000);

      // Type caption if provided
      if (message) {
        const editor = page.locator('div[role="textbox"]:visible, div[contenteditable="true"]:visible').first();
        if (await editor.count() > 0) {
          await editor.click();
          await page.waitForTimeout(500);
          await page.keyboard.type(message);
          await page.waitForTimeout(1000);
        }
      }

      // Click "Post"
      const posted = await clickPost();
      if (!posted) {
        return res.status(500).json({ error: 'Could not find the Post button after uploading photos' });
      }

      // Wait for upload + post to complete (photo uploads take longer)
      await settle(60000);
      res.json({ status: 'ok', posted: true, photo_count: images.length, message: 'Photo post submitted to personal profile' });
    } finally {
      for (const p of tmpPaths) {
        try { fs.unlinkSync(p); } catch (_) {}
      }
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal posting — link with optional message ─────────────────────

async function handlePostLink(req, res) {
  const { url, message, privacy } = req.body;
  if (!url) return res.status(400).json({ error: 'url is required' });
  try {
    await ensureBrowser();
    await page.goto('https://www.facebook.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Open the composer
    const composer = page.locator('[role="button"]:has-text("What\'s on your mind"), [aria-label*="on your mind"]').first();
    if (await composer.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the Facebook post composer' });
    }
    await composer.click();
    await page.waitForTimeout(2000);

    // Set privacy if specified
    if (privacy) {
      await setPrivacy(privacy);
    }

    // Type the message + URL into the composer — Facebook auto-generates a link preview
    const editor = page.locator('div[role="textbox"]:visible, div[contenteditable="true"]:visible').first();
    if (await editor.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the post text editor' });
    }
    await editor.click();
    await page.waitForTimeout(500);
    const text = message ? `${message}\n${url}` : url;
    await page.keyboard.type(text);
    await page.waitForTimeout(3000); // wait for link preview to generate

    // Click "Post"
    const posted = await clickPost();
    if (!posted) {
      return res.status(500).json({ error: 'Could not find the Post button' });
    }

    await settle();
    res.json({ status: 'ok', posted: true, url, message: 'Link post submitted to personal profile' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal posting — video with caption ─────────────────────────────

async function handlePostVideo(req, res) {
  const { message, video_base64, filename, privacy } = req.body;
  if (!video_base64) return res.status(400).json({ error: 'video_base64 is required' });
  try {
    await ensureBrowser();
    await page.goto('https://www.facebook.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Click "Photo/video" to open the photo/video upload dialog
    const photoVideoBtn = page.locator('[role="button"]:has-text("Photo/video"), [role="button"]:has-text("Photo/Video")').first();
    if (await photoVideoBtn.count() > 0) {
      await photoVideoBtn.click();
      await page.waitForTimeout(2000);
    }

    // Set privacy if specified
    if (privacy) {
      await setPrivacy(privacy);
    }

    // Upload the video via the file input
    const fileInput = page.locator('input[type="file"]').first();
    if (await fileInput.count() === 0) {
      await page.waitForTimeout(2000);
    }
    if (await fileInput.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the video file input' });
    }

    const buffer = Buffer.from(video_base64, 'base64');
    const ext = path.extname(filename || '.mp4') || '.mp4';
    const tmpPath = path.join(os.tmpdir(), `fb-sidecar-video-${Date.now()}${ext}`);
    fs.writeFileSync(tmpPath, buffer);

    try {
      await fileInput.setInputFiles(tmpPath);
      // Video uploads take significantly longer than photos
      await page.waitForTimeout(5000);

      // Type caption if provided
      if (message) {
        const editor = page.locator('div[role="textbox"]:visible, div[contenteditable="true"]:visible').first();
        if (await editor.count() > 0) {
          await editor.click();
          await page.waitForTimeout(500);
          await page.keyboard.type(message);
          await page.waitForTimeout(1000);
        }
      }

      // Click "Post"
      const posted = await clickPost();
      if (!posted) {
        return res.status(500).json({ error: 'Could not find the Post button after uploading video' });
      }

      // Video processing takes longer — wait up to 120s
      await settle(120000);
      res.json({ status: 'ok', posted: true, message: 'Video post submitted to personal profile' });
    } finally {
      try { fs.unlinkSync(tmpPath); } catch (_) {}
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Page management ────────────────────────────────────────────────────

async function handleListPages(req, res) {
  try {
    await ensureBrowser();
    // Use the Graph API-like page: /me/accounts is not available via browser,
    // so we navigate to the Pages management UI
    await page.goto('https://www.facebook.com/pages/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Extract page entries from the Pages management page
    const pages = await page.evaluate(() => {
      const results = [];
      // Facebook renders page cards with links to /<page-name> or /profile.php?id=<id>
      const links = document.querySelectorAll('a[href*="/profile.php?id="], a[href^="/"][href*="-"]');
      const seen = new Set();
      for (const link of links) {
        const href = link.getAttribute('href') || '';
        const text = (link.innerText || '').trim();
        if (text && text.length > 1 && !seen.has(href) && !href.includes('/settings') && !href.includes('/help')) {
          seen.add(href);
          // Try to extract page ID from the href
          const idMatch = href.match(/id=(\d+)/);
          results.push({
            name: text.substring(0, 100),
            url: href.startsWith('http') ? href : `https://www.facebook.com${href}`,
            page_id: idMatch ? idMatch[1] : null,
          });
        }
      }
      return results.slice(0, 20);
    }).catch(() => []);

    res.json({ status: 'ok', pages });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

async function handleUsePage(req, res) {
  const { page_id } = req.params;
  try {
    await ensureBrowser();

    // Navigate to the page, then use Facebook's "Switch to Page" feature
    // The i_user cookie approach (from fbpost) is more reliable, but
    // switching via the UI works for most pages.
    await page.goto(`https://www.facebook.com/profile.php?id=${page_id}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Click "Switch to Page" or "Log in as Page"
    const switchBtn = page.locator(
      '[role="button"]:has-text("Switch"), [role="button"]:has-text("Switch to Page"), ' +
      'a:has-text("Switch to Page"), [aria-label*="Switch"]'
    ).first();
    if (await switchBtn.count() > 0) {
      await switchBtn.click();
      await page.waitForTimeout(3000);
      activePageId = page_id;
      res.json({ status: 'ok', active_page_id: page_id, message: 'Switched to Page context' });
    } else {
      // Some pages allow posting directly without switching
      activePageId = page_id;
      res.json({ status: 'ok', active_page_id: page_id, message: 'Page context set (no explicit switch button found — posting will target this page)' });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

async function handlePagePostText(req, res) {
  const { message } = req.body;
  if (!message) return res.status(400).json({ error: 'message is required' });
  if (!activePageId) return res.status(400).json({ error: 'No active Page — call POST /page/:page_id/use first' });
  try {
    await ensureBrowser();
    // Navigate to the page's feed
    await page.goto(`https://www.facebook.com/profile.php?id=${activePageId}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Click the composer on the page
    const composer = page.locator('[role="button"]:has-text("What\'s on your mind"), [aria-label*="on your mind"]').first();
    if (await composer.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the Page post composer' });
    }
    await composer.click();
    await page.waitForTimeout(2000);

    const editor = page.locator('div[role="textbox"]:visible, div[contenteditable="true"]:visible').first();
    if (await editor.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the post text editor' });
    }
    await editor.click();
    await page.waitForTimeout(500);
    await page.keyboard.type(message);
    await page.waitForTimeout(1000);

    const posted = await clickPost();
    if (!posted) {
      return res.status(500).json({ error: 'Could not find the Post button' });
    }

    await settle();
    res.json({ status: 'ok', posted: true, page_id: activePageId, message: 'Text posted to Page' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

async function handlePagePostPhoto(req, res) {
  const { message, images } = req.body;
  if (!images || !Array.isArray(images) || images.length === 0) {
    return res.status(400).json({ error: 'images must be a non-empty array' });
  }
  if (!activePageId) return res.status(400).json({ error: 'No active Page — call POST /page/:page_id/use first' });
  try {
    await ensureBrowser();
    await page.goto(`https://www.facebook.com/profile.php?id=${activePageId}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    if (!isLoggedIn()) {
      return res.status(401).json({ error: 'Not logged in to Facebook' });
    }

    // Click "Photo/video" on the page composer
    const photoVideoBtn = page.locator('[role="button"]:has-text("Photo/video"), [role="button"]:has-text("Photo/Video")').first();
    if (await photoVideoBtn.count() > 0) {
      await photoVideoBtn.click();
      await page.waitForTimeout(2000);
    }

    const fileInput = page.locator('input[type="file"][accept*="image"]').first();
    if (await fileInput.count() === 0) {
      await page.waitForTimeout(2000);
    }
    if (await fileInput.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the photo file input' });
    }

    const tmpPaths = [];
    try {
      for (const img of images) {
        const buffer = Buffer.from(img.image_base64, 'base64');
        const tmpPath = bufferToTempFile(buffer, img.filename || `page-photo-${Date.now()}.jpg`);
        tmpPaths.push(tmpPath);
      }
      await fileInput.setInputFiles(tmpPaths);
      await page.waitForTimeout(3000);

      if (message) {
        const editor = page.locator('div[role="textbox"]:visible, div[contenteditable="true"]:visible').first();
        if (await editor.count() > 0) {
          await editor.click();
          await page.waitForTimeout(500);
          await page.keyboard.type(message);
          await page.waitForTimeout(1000);
        }
      }

      const posted = await clickPost();
      if (!posted) {
        return res.status(500).json({ error: 'Could not find the Post button' });
      }

      await settle(60000);
      res.json({ status: 'ok', posted: true, page_id: activePageId, photo_count: images.length, message: 'Photo posted to Page' });
    } finally {
      for (const p of tmpPaths) {
        try { fs.unlinkSync(p); } catch (_) {}
      }
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── Helpers: privacy + post button ─────────────────────────────────────────

/** Set the privacy of the current composer: 'public' | 'friends' | 'only_me' */
async function setPrivacy(privacy) {
  try {
    // Click the privacy selector button (shows current audience like "Friends")
    const privacyBtn = page.locator(
      '[role="button"][aria-label*="Friends"], [role="button"][aria-label*="Public"], ' +
      '[role="button"][aria-label*="Only me"], [aria-label*="privacy"], ' +
      'div[aria-label*="Privacy"]:visible'
    ).first();
    if (await privacyBtn.count() === 0) return false;
    await privacyBtn.click();
    await page.waitForTimeout(1500);

    // Click the matching option in the dropdown
    const labelMap = {
      public: 'Public',
      friends: 'Friends',
      only_me: 'Only me',
    };
    const targetLabel = labelMap[privacy] || privacy;
    const option = page.locator(`[role="menuitem"]:has-text("${targetLabel}"), [role="option"]:has-text("${targetLabel}"), div:has-text("${targetLabel}")`).first();
    if (await option.count() > 0) {
      await option.click();
      await page.waitForTimeout(1000);
      return true;
    }
    // Close the dropdown if we didn't find the option
    await page.keyboard.press('Escape');
    return false;
  } catch (_) {
    return false;
  }
}

/** Find and click the "Post" button in the composer dialog. */
async function clickPost() {
  for (const sel of [
    'div[role="dialog"] button:has-text("Post")',
    'div[role="dialog"] [role="button"]:has-text("Post")',
    'button[type="submit"]:has-text("Post")',
    'button:has-text("Post")',
    '[role="button"]:has-text("Post")',
  ]) {
    const btn = page.locator(sel).first();
    if (await btn.isVisible().catch(() => false)) {
      // Make sure it's not the "Post" tab/label — check it's a button-like element
      const tagName = await btn.evaluate((el) => el.tagName.toLowerCase()).catch(() => '');
      if (tagName === 'button' || (await btn.getAttribute('role')) === 'button') {
        await btn.click();
        await page.waitForTimeout(2000);
        return true;
      }
    }
  }
  return false;
}

// ── Server setup ───────────────────────────────────────────────────────────

const app = express();
app.use(express.json({ limit: '50mb' }));

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'facebook-browser-sidecar', has_session: !!storageState, active_page_id: activePageId });
});

// Session
app.post('/session', handleSetSession);
app.get('/session', handleCheckSession);
app.post('/login', handleLogin);

// Personal profile
app.get('/profile', handleReadProfile);
app.post('/profile/bio', handleUpdateBio);
app.post('/profile/picture', handleUploadPicture);
app.post('/profile/cover', handleUploadCover);
app.post('/profile/website', handleUpdateWebsite);

// Personal posting
app.post('/post/text', handlePostText);
app.post('/post/photo', handlePostPhoto);
app.post('/post/link', handlePostLink);
app.post('/post/video', handlePostVideo);

// Page mode
app.get('/pages', handleListPages);
app.post('/page/:page_id/use', handleUsePage);
app.post('/page/post/text', handlePagePostText);
app.post('/page/post/photo', handlePagePostPhoto);

// Debug: screenshot
app.get('/screenshot', async (req, res) => {
  try {
    await ensureBrowser();
    const buf = await page.screenshot({ type: 'png' });
    res.set('Content-Type', 'image/png');
    res.send(buf);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Debug: page text
app.get('/debug/page-text', async (req, res) => {
  try {
    await ensureBrowser();
    const text = await page.innerText('body');
    const url = page.url();
    res.json({ url, text: text.substring(0, 2000) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Debug: inspect HTML around a text label
app.get('/debug/inspect', async (req, res) => {
  try {
    await ensureBrowser();
    const searchText = req.query.text || 'Bio';
    const html = await page.evaluate((searchText) => {
      const results = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (node.textContent && node.textContent.trim() === searchText) {
          // Walk up to find a container with edit controls
          let el = node.parentElement;
          for (let i = 0; i < 5 && el; i++) {
            const editBtns = el.querySelectorAll('[role="button"], button, a[href]');
            if (editBtns.length > 0) {
              results.push({
                level: i,
                tag: el.tagName,
                class: (el.className || '').toString().substring(0, 100),
                role: el.getAttribute('role'),
                html: el.outerHTML.substring(0, 800),
                buttons: Array.from(editBtns).map(b => ({
                  tag: b.tagName,
                  text: (b.innerText || '').substring(0, 50),
                  role: b.getAttribute('role'),
                  ariaLabel: b.getAttribute('aria-label'),
                  href: b.getAttribute('href'),
                })),
              });
              break;
            }
            el = el.parentElement;
          }
        }
      }
      return results;
    }, searchText);
    res.json({ url: page.url(), searchText, results: html });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Debug: list all textareas and contenteditable elements
app.get('/debug/inputs', async (req, res) => {
  try {
    await ensureBrowser();
    const inputs = await page.evaluate(() => {
      const results = [];
      document.querySelectorAll('textarea, [contenteditable="true"], input[type="text"]').forEach(el => {
        results.push({
          tag: el.tagName,
          type: el.getAttribute('type'),
          ariaLabel: el.getAttribute('aria-label'),
          placeholder: el.getAttribute('placeholder'),
          name: el.getAttribute('name'),
          id: el.id,
          className: (el.className || '').toString().substring(0, 80),
          visible: el.offsetParent !== null,
          text: (el.innerText || el.value || '').substring(0, 50),
        });
      });
      return results;
    });
    res.json({ url: page.url(), inputs });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Graceful shutdown
process.on('SIGTERM', async () => {
  await closeBrowser();
  process.exit(0);
});

app.listen(PORT, () => {
  console.log(`Facebook browser sidecar listening on port ${PORT}`);
});
