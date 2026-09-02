/**
 * TikTok Browser Automation Sidecar
 *
 * REST API that drives a persistent Playwright browser session for TikTok.
 * Handles all non-captcha-gated settings: privacy, notifications, ads,
 * business verification, accessibility, and profile reads.
 *
 * Captcha-gated writes (bio, avatar, nickname) are NOT supported —
 * those require human interaction with TikTok's slider captcha.
 */

import express from 'express';
import { chromium } from 'playwright';

const PORT = process.env.TIKTOK_SIDECAR_PORT || 9224;
const TIKTOK_PROFILE_URL = 'https://www.tiktok.com/@user3113682023385';
const TIKTOK_SETTINGS_URL = 'https://www.tiktok.com/setting?lang=en';
const TIKTOK_BIZ_REG_URL = 'https://www.tiktok.com/business-suite/business-registration/verify?source=onboarding';

// ── Browser lifecycle ─────────────────────────────────────────────────────

let browser = null;
let context = null;
let page = null;
let sessionId = null;
let userId = null;

async function ensureBrowser() {
  if (browser && browser.isConnected()) return;

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

  context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    locale: 'en-US',
    timezoneId: 'Europe/Athens',
    extraHTTPHeaders: { 'Accept-Language': 'en-US,en;q=0.9' },
    permissions: ['notifications'],
  });

  page = await context.newPage();

  // Inject session cookie if available
  if (sessionId) {
    await context.addCookies([{
      name: 'sessionid',
      value: sessionId,
      domain: '.tiktok.com',
      path: '/',
      httpOnly: true,
      secure: true,
      sameSite: 'Lax',
    }]);
  }
}

async function closeBrowser() {
  if (browser) {
    await browser.close().catch(() => {});
    browser = null;
    context = null;
    page = null;
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function gotoSettings() {
  await ensureBrowser();
  await page.goto(TIKTOK_SETTINGS_URL, { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(3000);
}

async function gotoProfile() {
  await ensureBrowser();
  await page.goto(TIKTOK_PROFILE_URL, { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(3000);
}

/** Click a switch/toggle by its label text and return the new checked state. */
async function clickSwitch(labelText) {
  // The label text is in a sibling/parent element, not on the switch itself.
  // Find the text element, then navigate to the switch in the same row.
  const textEl = page.locator(`text=${labelText}`).first();
  await textEl.waitFor({ state: 'visible', timeout: 10000 });
  // Go up to find the row container, then find the switch inside it
  const row = textEl.locator('xpath=ancestor::div[contains(@class,"row") or contains(@class,"cell") or contains(@class,"container")][1]').first();
  let sw = row.locator('[role="switch"]').first();
  // Fallback: try going up multiple levels
  if (!(await sw.isVisible().catch(() => false))) {
    sw = textEl.locator('xpath=ancestor::*[.//*[@role="switch"]][1]').first().locator('[role="switch"]').first();
  }
  if (!(await sw.isVisible().catch(() => false))) {
    // Last resort: find the nearest switch in the DOM after the text
    sw = textEl.locator('xpath=following::*[@role="switch"][1]').first();
  }
  await sw.waitFor({ state: 'visible', timeout: 10000 });
  const wasChecked = await sw.getAttribute('aria-checked');
  await sw.click();
  await page.waitForTimeout(1500);
  const nowChecked = await sw.getAttribute('aria-checked');
  return { label: labelText, before: wasChecked === 'true', after: nowChecked === 'true' };
}

/** Click a switch by its ref position in the settings page (for unlabeled switches). */
async function clickSwitchByIndex(index) {
  const switches = page.locator('[role="switch"]');
  const sw = switches.nth(index);
  await sw.waitFor({ state: 'visible', timeout: 10000 });
  const wasChecked = await sw.getAttribute('aria-checked');
  await sw.click();
  await page.waitForTimeout(1500);
  const nowChecked = await sw.getAttribute('aria-checked');
  return { index, before: wasChecked === 'true', after: nowChecked === 'true' };
}

/** Check if a captcha dialog is present. */
async function hasCaptcha() {
  const dialogs = await page.locator('[role="dialog"]').allTextContents();
  return dialogs.some(t => t.includes('Drag the slider') || t.includes('fit the puzzle'));
}

/** Close all dialogs by pressing Escape. */
async function closeDialogs() {
  for (let i = 0; i < 5; i++) {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
  }
}

// ── API: Session ───────────────────────────────────────────────────────────

/** Set the TikTok session cookie and verify the session is alive. */
async function handleSetSession(req, res) {
  const { session_id, user_id } = req.body;
  if (!session_id) {
    return res.status(400).json({ error: 'session_id is required' });
  }
  sessionId = session_id;
  userId = user_id || null;
  await closeBrowser();
  await ensureBrowser();
  try {
    await page.goto(TIKTOK_PROFILE_URL, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(3000);
    const title = await page.title();
    const isLoggedIn = !title.includes('Log in') && !page.url().includes('login');
    res.json({ status: 'ok', logged_in: isLoggedIn, profile_url: page.url(), title });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

/** Check if the current session is still alive. */
async function handleCheckSession(req, res) {
  try {
    await gotoProfile();
    const title = await page.title();
    const isLoggedIn = !title.includes('Log in') && !page.url().includes('login');
    res.json({ status: 'ok', logged_in: isLoggedIn, profile_url: page.url(), title });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Profile read (from browser) ───────────────────────────────────────

async function handleReadProfile(req, res) {
  try {
    await gotoProfile();
    const nameEl = page.locator('h1').first();
    const usernameEl = page.locator('h2').first();
    const bioEl = page.locator('h2').nth(1);
    const name = await nameEl.textContent().catch(() => null);
    const username = await usernameEl.textContent().catch(() => null);
    const bio = await bioEl.textContent().catch(() => null);
    // Get avatar URL from the profile image
    const avatarSrc = await page.locator('img[alt*="Avatar"], img[src*="tiktok"]').first()
      .getAttribute('src').catch(() => null);
    res.json({
      status: 'ok',
      profile: { name, username, bio, avatar_url: avatarSrc },
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Privacy settings ──────────────────────────────────────────────────

async function handleSetPrivateAccount(req, res) {
  const { enabled } = req.body;
  try {
    await gotoSettings();
    // Find the "Private account" text and navigate to its row's switch
    const textEl = page.locator('text=Private account').first();
    await textEl.waitFor({ state: 'visible', timeout: 10000 });
    // Try multiple ancestor levels to find the switch
    const sw = textEl.locator('xpath=ancestor::*[.//*[@role="switch"]][1]').first().locator('[role="switch"]').first();
    await sw.waitFor({ state: 'visible', timeout: 10000 });
    const current = await sw.getAttribute('aria-checked');
    const wantOn = enabled === true || enabled === 'true';
    if ((current === 'true') !== wantOn) {
      await sw.click();
      await page.waitForTimeout(1500);
    }
    const after = await sw.getAttribute('aria-checked');
    res.json({ status: 'ok', setting: 'private_account', enabled: after === 'true' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

async function handleSetComments(req, res) {
  const { permission } = req.body; // "Everyone" or "Friends"
  if (!['Everyone', 'Friends'].includes(permission)) {
    return res.status(400).json({ error: 'permission must be "Everyone" or "Friends"' });
  }
  try {
    await gotoSettings();
    // Click on the Comments row to open the dialog
    const commentsText = page.locator('text=Who can comment on your posts').first();
    await commentsText.waitFor({ state: 'visible', timeout: 10000 });
    const commentsRow = commentsText.locator('xpath=ancestor::*[position()=1]').first();
    await commentsRow.click();
    await page.waitForTimeout(2000);
    // Use JavaScript to find and click the radio inside the dialog
    const result = await page.evaluate((perm) => {
      const dialogs = document.querySelectorAll('[role="dialog"]');
      const dialog = dialogs[dialogs.length - 1];
      if (!dialog) return { found: false, reason: 'no dialog' };
      const radios = dialog.querySelectorAll('[role="radio"]');
      for (const radio of radios) {
        const container = radio.closest('div');
        const text = container ? container.textContent : '';
        if (text.includes(perm)) {
          const isChecked = radio.getAttribute('aria-checked') === 'true';
          if (!isChecked) radio.click();
          return { found: true, wasChecked: isChecked, clicked: !isChecked };
        }
      }
      return { found: false, reason: 'option not found', radioCount: radios.length };
    }, permission);
    await page.waitForTimeout(1000);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
    res.json({ status: 'ok', setting: 'comments', permission, ...result });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

async function handleSetDirectMessages(req, res) {
  const { potential_connections, others } = req.body;
  try {
    await page.goto('https://www.tiktok.com/setting/privacy/direct-messages', {
      waitUntil: 'networkidle', timeout: 60000,
    });
    await page.waitForTimeout(3000);

    async function selectRadio(label) {
      if (!label) return null;
      const result = await page.evaluate((lbl) => {
        const radios = document.querySelectorAll('[role="radio"]');
        for (const radio of radios) {
          const container = radio.closest('div');
          const text = container ? container.textContent : '';
          if (text.includes(lbl)) {
            const isChecked = radio.getAttribute('aria-checked') === 'true';
            if (!isChecked) radio.click();
            return { found: true, wasChecked: isChecked, clicked: !isChecked };
          }
        }
        return { found: false };
      }, label);
      if (result.found) await page.waitForTimeout(1000);
      return result;
    }

    const pcResult = await selectRadio(potential_connections);
    const othersResult = await selectRadio(others);

    res.json({
      status: 'ok',
      setting: 'direct_messages',
      potential_connections: pcResult?.found ? potential_connections : null,
      others: othersResult?.found ? others : null,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Push notifications ────────────────────────────────────────────────

async function handleSetDesktopNotifications(req, res) {
  const { enabled } = req.body;
  try {
    await gotoSettings();
    const result = await clickSwitch('Allow in browser');
    res.json({ status: 'ok', setting: 'desktop_notifications', enabled: result.after });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

async function handleSetInteractionNotifications(req, res) {
  const { likes, comments, new_followers, mentions_and_tags } = req.body;
  try {
    await gotoSettings();
    // Click the "Interactions" button to expand the panel
    const interactionsBtn = page.getByRole('button', { name: 'Interactions' }).first();
    await interactionsBtn.click();
    await page.waitForTimeout(1500);

    const settings = {};
    const toggles = { likes, comments, new_followers, mentions_and_tags };
    const labels = {
      likes: 'Likes', comments: 'Comments',
      new_followers: 'New followers', mentions_and_tags: 'Mentions and tags',
    };

    for (const [key, desired] of Object.entries(toggles)) {
      if (desired === undefined || desired === null) continue;
      const wantOn = desired === true || desired === 'true';
      const textEl = page.locator(`text=${labels[key]}`).first();
      if (await textEl.isVisible().catch(() => false)) {
        const sw = textEl.locator('xpath=ancestor::*[.//*[@role="switch"]][1]').first().locator('[role="switch"]').first();
        if (await sw.isVisible().catch(() => false)) {
          const current = await sw.getAttribute('aria-checked');
          if ((current === 'true') !== wantOn) {
            await sw.click();
            await page.waitForTimeout(800);
          }
          const after = await sw.getAttribute('aria-checked');
          settings[key] = after === 'true';
        }
      }
    }

    // Close the panel
    await interactionsBtn.click();
    await page.waitForTimeout(500);
    res.json({ status: 'ok', setting: 'interaction_notifications', ...settings });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Ads settings ──────────────────────────────────────────────────────

async function handleSetPersonalizedAds(req, res) {
  const { enabled } = req.body;
  try {
    await gotoSettings();
    const textEl = page.locator('text=Personalized ads').first();
    await textEl.waitFor({ state: 'visible', timeout: 10000 });
    const sw = textEl.locator('xpath=ancestor::*[.//*[@role="switch"]][1]').first().locator('[role="switch"]').first();
    await sw.waitFor({ state: 'visible', timeout: 10000 });
    const current = await sw.getAttribute('aria-checked');
    const wantOn = enabled === true || enabled === 'true';
    if ((current === 'true') !== wantOn) {
      await sw.click();
      await page.waitForTimeout(1500);
    }
    const after = await sw.getAttribute('aria-checked');
    res.json({ status: 'ok', setting: 'personalized_ads', enabled: after === 'true' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Accessibility ─────────────────────────────────────────────────────

async function handleSetColorContrast(req, res) {
  const { enabled } = req.body;
  try {
    await gotoSettings();
    const result = await clickSwitch('Increase color contrast');
    res.json({ status: 'ok', setting: 'color_contrast', enabled: result.after });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Business verification ─────────────────────────────────────────────

async function handleBusinessVerificationFill(req, res) {
  const {
    company_name, website, country, address, industry,
    business_license_number,
  } = req.body;
  try {
    await ensureBrowser();
    await page.goto(TIKTOK_BIZ_REG_URL, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(3000);

    // Accept terms if checkbox is present
    const checkbox = page.locator('[type="checkbox"]').first();
    if (await checkbox.isVisible().catch(() => false)) {
      await checkbox.click();
      await page.waitForTimeout(500);
      // Click "Get started" if present
      const getStarted = page.getByRole('button', { name: 'Get started' });
      if (await getStarted.isVisible().catch(() => false)) {
        await getStarted.click();
        await page.waitForTimeout(3000);
      }
    }

    const filled = {};

    // Company name
    if (company_name) {
      const nameInput = page.getByTestId('field-companyName');
      if (await nameInput.isVisible().catch(() => false)) {
        await nameInput.fill(company_name);
        filled.company_name = company_name;
      }
    }

    // Website
    if (website) {
      const webInput = page.getByTestId('field-webSite');
      if (await webInput.isVisible().catch(() => false)) {
        await webInput.fill(website);
        filled.website = website;
      }
    }

    // Country (combobox)
    if (country) {
      const countryCombo = page.locator('[role="combobox"]').first();
      if (await countryCombo.isVisible().catch(() => false)) {
        await countryCombo.click();
        await page.waitForTimeout(1000);
        const opt = page.locator(`[role="option"]`, { hasText: country }).first();
        if (await opt.isVisible().catch(() => false)) {
          await opt.click();
          filled.country = country;
          await page.waitForTimeout(500);
        }
      }
    }

    // Address
    if (address) {
      const addressInput = page.locator('[role="textbox"]').nth(1);
      if (await addressInput.isVisible().catch(() => false)) {
        await addressInput.fill(address);
        filled.address = address;
      }
    }

    // Industry (combobox — restricted industries)
    if (industry) {
      const industryCombo = page.getByTestId('field-industryCode.code');
      if (await industryCombo.isVisible().catch(() => false)) {
        await industryCombo.click();
        await page.waitForTimeout(1000);
        const opt = page.locator(`[role="option"]`, { hasText: industry }).first();
        if (await opt.isVisible().catch(() => false)) {
          await opt.click();
          filled.industry = industry;
          await page.waitForTimeout(500);
        }
      }
    }

    // Business license number
    if (business_license_number) {
      const licenseInput = page.getByTestId('field-businessLicenseNo');
      if (await licenseInput.isVisible().catch(() => false)) {
        await licenseInput.fill(business_license_number);
        filled.business_license_number = business_license_number;
      }
    }

    res.json({
      status: 'ok',
      message: 'Form filled. Company certification document upload and submit require manual action.',
      filled_fields: filled,
      requires_manual: ['company_certification_document', 'submit'],
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

async function handleBusinessVerificationStatus(req, res) {
  try {
    await gotoSettings();
    // Check the Business verification switch state
    const bvSection = page.locator('text=Business verification').locator('..').locator('..');
    const sw = bvSection.locator('[role="switch"]').first();
    if (await sw.isVisible().catch(() => false)) {
      const checked = await sw.getAttribute('aria-checked');
      res.json({ status: 'ok', verified: checked === 'true' });
    } else {
      res.json({ status: 'ok', verified: false, note: 'Business verification section not found' });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Read all current settings ─────────────────────────────────────────

async function handleReadAllSettings(req, res) {
  try {
    await gotoSettings();
    await page.waitForTimeout(2000);

    // Collect all switch states
    const switches = await page.locator('[role="switch"]').all();
    const switchStates = [];
    for (let i = 0; i < switches.length; i++) {
      const sw = switches[i];
      const label = await sw.getAttribute('aria-label') ||
                    await sw.locator('..').textContent().catch(() => `switch_${i}`);
      const checked = await sw.getAttribute('aria-checked');
      switchStates.push({ index: i, label: label?.substring(0, 60), checked: checked === 'true' });
    }

    // Read text values
    const commentsValue = await page.locator('text=Who can comment on your posts')
      .locator('..').locator('..').textContent().catch(() => null);

    res.json({
      status: 'ok',
      switches: switchStates,
      comments: commentsValue?.includes('Everyone') ? 'Everyone' :
                commentsValue?.includes('Friends') ? 'Friends' : 'unknown',
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── Server setup ───────────────────────────────────────────────────────────

const app = express();
app.use(express.json({ limit: '10mb' }));

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'tiktok-browser-sidecar', has_session: !!sessionId });
});

// Session
app.post('/session', handleSetSession);
app.get('/session', handleCheckSession);

// Profile read (from browser — includes avatar URL)
app.get('/profile', handleReadProfile);

// Privacy
app.post('/privacy/private-account', handleSetPrivateAccount);
app.post('/privacy/comments', handleSetComments);
app.post('/privacy/direct-messages', handleSetDirectMessages);

// Push notifications
app.post('/notifications/desktop', handleSetDesktopNotifications);
app.post('/notifications/interactions', handleSetInteractionNotifications);

// Ads
app.post('/ads/personalized', handleSetPersonalizedAds);

// Accessibility
app.post('/accessibility/contrast', handleSetColorContrast);

// Business verification
app.post('/business-verification/fill', handleBusinessVerificationFill);
app.get('/business-verification/status', handleBusinessVerificationStatus);

// Read all settings
app.get('/settings', handleReadAllSettings);

// Graceful shutdown
process.on('SIGTERM', async () => {
  await closeBrowser();
  process.exit(0);
});

app.listen(PORT, () => {
  console.log(`TikTok browser sidecar listening on port ${PORT}`);
});
