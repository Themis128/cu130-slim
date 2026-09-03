/**
 * LinkedIn Browser Automation Sidecar
 *
 * REST API that drives a persistent Playwright browser session for LinkedIn.
 * Supports BOTH personal profiles and Company/Organization pages.
 *
 * Personal profile endpoints:
 *   GET  /health
 *   POST /session              — set storage state (cookies + localStorage)
 *   GET  /session              — check if session is alive
 *   GET  /profile              — read full personal profile
 *   POST /profile/headline     — update headline
 *   POST /profile/about        — update About section
 *   POST /profile/cover        — upload cover/background photo
 *   POST /profile/picture      — upload profile photo (with crop handling)
 *   POST /profile/website      — update website in contact info
 *   POST /profile/location     — update location
 *   POST /profile/experience   — add a work experience entry
 *   POST /profile/education    — add an education entry
 *   POST /profile/skills       — add a skill
 *
 * Company page endpoints:
 *   GET  /company/:vanity      — read company page info
 *   POST /company/:vanity/about    — update company About
 *   POST /company/:vanity/website  — update company website
 *   POST /company/:vanity/industry — update company industry
 *   POST /company/:vanity/specialties — update company specialties
 *   POST /company/:vanity/logo     — upload company logo
 *   POST /company/:vanity/cover    — upload company cover photo
 */

import express from 'express';
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import os from 'os';

const PORT = process.env.LINKEDIN_SIDECAR_PORT || 9225;

// ── Browser lifecycle ─────────────────────────────────────────────────────

let browser = null;
let context = null;
let page = null;
let storageState = null;

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

/** Best-effort wait for page to settle (LinkedIn has long-polling). */
async function settle(timeout = 20000) {
  try {
    await page.waitForLoadState('networkidle', { timeout });
  } catch (_) {}
  await page.waitForTimeout(1500);
}

/** Save a Buffer to a temp file and return the path. */
function bufferToTempFile(buffer, filename) {
  const ext = path.extname(filename) || '.jpg';
  const tmp = path.join(os.tmpdir(), `li-sidecar-${Date.now()}${ext}`);
  fs.writeFileSync(tmp, buffer);
  return tmp;
}

/** Click the first visible element matching a selector. */
async function clickFirst(selector, timeout = 10000) {
  const loc = page.locator(selector).first();
  await loc.waitFor({ state: 'visible', timeout });
  await loc.click();
  return loc;
}

/** Find and click a Save button in a dialog or page. */
async function clickSave() {
  for (const sel of [
    'div[role="dialog"] button:has-text("Save")',
    'button[type="submit"]:has-text("Save")',
    'button:has-text("Save")',
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

// ── API: Session ───────────────────────────────────────────────────────────

async function handleSetSession(req, res) {
  const { storage_state } = req.body;
  if (!storage_state) {
    return res.status(400).json({ error: 'storage_state is required' });
  }
  storageState = storage_state;
  await closeBrowser();
  await ensureBrowser();
  try {
    await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();
    const loggedIn = !page.url().includes('/login') && !page.url().includes('/checkpoint');
    res.json({ status: 'ok', logged_in: loggedIn, url: page.url() });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

async function handleCheckSession(req, res) {
  try {
    await ensureBrowser();
    await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();
    const loggedIn = !page.url().includes('/login') && !page.url().includes('/checkpoint');
    res.json({ status: 'ok', logged_in: loggedIn, url: page.url() });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal profile read ─────────────────────────────────────────────

async function handleReadProfile(req, res) {
  try {
    await ensureBrowser();
    await page.goto('https://www.linkedin.com/in/me/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    const result = { url: page.url, name: null, headline: null, about: null, location: null, website: null };

    // Name: try h1 first, then h2 in top-card section
    const nameH1 = page.locator('h1.text-heading-xlarge').first();
    if (await nameH1.count() > 0) {
      result.name = (await nameH1.innerText()).trim();
    }
    if (!result.name) {
      const secs = page.locator('section:has(h2)');
      for (let i = 0; i < (await secs.count()); i++) {
        const sec = secs.nth(i);
        const h2 = (await sec.locator('h2').first.innerText()).trim();
        if (h2 && !h2.endsWith('notifications')) {
          result.name = h2;
          const lines = (await sec.innerText()).split('\n').map((l) => l.trim()).filter(Boolean);
          for (let j = 0; j < lines.length; j++) {
            if (lines[j] === h2 && j + 1 < lines.length) {
              result.headline = lines[j + 1];
              break;
            }
          }
          break;
        }
      }
    }

    // Headline via .text-body-medium
    if (!result.headline) {
      const headlineEl = page.locator('.text-body-medium').first();
      if (await headlineEl.count() > 0) {
        result.headline = (await headlineEl.innerText()).trim();
      }
    }

    // Location
    const locEl = page.locator('.text-body-small.inline.t-black--light.break-words').first();
    if (await locEl.count() > 0) {
      result.location = (await locEl.innerText()).trim();
    }

    // About
    const aboutSection = page.locator("section:has(h2:has-text('About'))").first();
    if (await aboutSection.count() > 0) {
      const text = await aboutSection.innerText();
      const lines = text.split('\n');
      const start = lines.indexOf('About') + 1;
      let end = lines.length;
      for (const marker of ['Featured', 'Activity', 'Experience']) {
        const idx = lines.indexOf(marker);
        if (idx > start) end = Math.min(end, idx);
      }
      result.about = lines.slice(start, end).join('\n').trim() || null;
    }

    // Website (from contact info)
    try {
      const contactBtn = page.locator('a:has-text("Contact info"), button:has-text("Contact info")').first();
      if (await contactBtn.isVisible().catch(() => false)) {
        await contactBtn.click();
        await page.waitForTimeout(2000);
        const websiteEl = page.locator('div[role="dialog"] a[href^="http"]').first();
        if (await websiteEl.isVisible().catch(() => false)) {
          result.website = await websiteEl.getAttribute('href');
        }
        await page.keyboard.press('Escape');
        await page.waitForTimeout(500);
      }
    } catch (_) {}

    res.json({ status: 'ok', profile: result });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal headline ─────────────────────────────────────────────────

async function handleUpdateHeadline(req, res) {
  const { headline } = req.body;
  if (!headline) return res.status(400).json({ error: 'headline is required' });
  try {
    await ensureBrowser();
    await page.goto('https://www.linkedin.com/in/me/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    // Click the "Update headline" prompt or the edit intro pencil
    const updatePrompt = page.getByText('Update headline', { exact: true }).first();
    const editPencil = page.locator('button[aria-label*="Edit intro"], button[aria-label*="edit intro"]').first();

    if (await updatePrompt.count() > 0) {
      const handle = await updatePrompt.evaluateHandle(
        "el => el.closest('button, a, [role=\\'button\\']') || el.parentElement"
      );
      await page.evaluate('el => el.click()', handle);
    } else if (await editPencil.count() > 0) {
      await editPencil.click();
    } else {
      return res.status(404).json({ error: 'Could not locate the LinkedIn headline/intro editor' });
    }
    await page.waitForTimeout(4000);

    // Find the headline editor in the dialog
    let editor = null;
    for (const sel of [
      "div[role='dialog'] textarea:visible",
      "textarea:visible",
      "div[role='dialog'] input:visible",
      '[contenteditable="true"]:visible',
    ]) {
      const loc = page.locator(sel);
      if (await loc.count() > 0) {
        editor = loc.first();
        break;
      }
    }
    if (!editor) return res.status(404).json({ error: 'Could not locate the headline input field' });

    // The dialog may have multiple fields (first name, last name, headline, etc.)
    // The headline is typically the 3rd input or has an aria-label containing "headline"
    const allEditors = await page.locator("div[role='dialog'] textarea:visible, div[role='dialog'] input:visible").all();
    if (allEditors.length > 1) {
      // Try to find the one with aria-label containing "headline" or "Headline"
      for (const e of allEditors) {
        const label = await e.getAttribute('aria-label').catch(() => null);
        if (label && label.toLowerCase().includes('headline')) {
          editor = e;
          break;
        }
      }
      // Fallback: the last textarea in the dialog is usually the headline
      if (editor === page.locator("div[role='dialog'] textarea:visible").first() && allEditors.length >= 3) {
        editor = allEditors[allEditors.length - 1];
      }
    }

    await editor.fill(headline);
    await clickSave();
    res.json({ status: 'ok', updated: ['headline'] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal About ────────────────────────────────────────────────────

async function handleUpdateAbout(req, res) {
  const { about } = req.body;
  if (!about) return res.status(400).json({ error: 'about is required' });
  try {
    await ensureBrowser();
    await page.goto('https://www.linkedin.com/in/me/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    const aboutBtn = page.locator('button[aria-label="Edit about"], a[aria-label="Edit about"]').first();
    if (await aboutBtn.count() === 0) {
      // Try alternative: a pencil icon in the About section
      const aboutSection = page.locator("section:has(h2:has-text('About'))").first();
      const pencil = aboutSection.locator('button[aria-label*="Edit"], button:has(svg[type="icon"])').first();
      if (await pencil.count() > 0) {
        await pencil.click();
      } else {
        return res.status(404).json({ error: 'Could not locate the LinkedIn "Edit about" button' });
      }
    } else {
      await aboutBtn.click();
    }
    await page.waitForTimeout(5000);

    const editor = page.locator('[contenteditable="true"]').first();
    if (await editor.count() === 0) {
      // Fallback: textarea
      const ta = page.locator('textarea:visible').first();
      if (await ta.count() === 0) {
        return res.status(404).json({ error: 'Could not locate the About editor' });
      }
      await ta.fill(about);
    } else {
      await editor.click();
      // Clear existing content
      await page.keyboard.press('Control+a');
      await page.keyboard.press('Delete');
      await page.keyboard.type(about);
    }

    await clickSave();
    res.json({ status: 'ok', updated: ['about'] });
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
    await page.goto('https://www.linkedin.com/in/me/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    // Click the "Edit cover photo" or camera icon on the cover area
    const coverEdit = page.locator(
      'button[aria-label*="cover"], button[aria-label*="Cover"], ' +
      'a[aria-label*="cover"], a[aria-label*="Cover"], ' +
      '.profile-background-editing-container button, ' +
      'button:has-text("Add cover photo"), button:has-text("Edit cover photo")'
    ).first();

    if (await coverEdit.count() === 0) {
      // Hover over the cover area to reveal the edit button
      const coverArea = page.locator('.profile-topcard-background-image, .pv-profile-background, [class*="cover"]').first();
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

    // Upload the file
    const buffer = Buffer.from(image_base64, 'base64');
    const tmpPath = bufferToTempFile(buffer, filename || 'cover.jpg');

    try {
      const fileInput = page.locator('input[type="file"][accept*="image"]').first();
      await fileInput.setInputFiles(tmpPath);
      await page.waitForTimeout(3000);

      // Handle crop/adjust step — click "Apply" or "Save"
      for (const btnText of ['Apply', 'Save', 'Done', 'Next']) {
        const btn = page.locator(`button:has-text("${btnText}")`).first();
        if (await btn.isVisible().catch(() => false)) {
          await btn.click();
          await page.waitForTimeout(2000);
          break;
        }
      }

      // Final save
      await clickSave();
      res.json({ status: 'ok', updated: ['cover'] });
    } finally {
      fs.unlinkSync(tmpPath);
    }
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
    await page.goto('https://www.linkedin.com/in/me/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    // Click the profile photo edit button (camera icon / "Edit photo")
    const picEdit = page.locator(
      'button[aria-label*="profile photo"], button[aria-label*="Edit photo"], ' +
      'button[aria-label*="Change photo"], a[aria-label*="profile photo"], ' +
      '.pv-top-card-profile-picture__edit-btn, ' +
      'button:has-text("Add photo"), button:has-text("Edit photo"), button:has-text("Change photo")'
    ).first();

    if (await picEdit.count() === 0) {
      // Hover over the profile picture area
      const picArea = page.locator('.pv-top-card-profile-picture, [class*="profile-photo"], .profile-photo-edit').first();
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

    // Click "Upload" or "Frame" if a submenu appears
    const uploadOption = page.locator('button:has-text("Upload"), button:has-text("Frame"), [data-control-name="upload_photo"]').first();
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

      // Handle crop step — LinkedIn shows a crop dialog with "Apply" then "Save"
      for (const btnText of ['Apply', 'Save', 'Done']) {
        const btn = page.locator(`div[role="dialog"] button:has-text("${btnText}"), button:has-text("${btnText}")`).first();
        if (await btn.isVisible().catch(() => false)) {
          await btn.click();
          await page.waitForTimeout(2000);
        }
      }

      res.json({ status: 'ok', updated: ['profile_picture'] });
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
    await page.goto('https://www.linkedin.com/in/me/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    // Open contact info dialog
    const contactBtn = page.locator('a:has-text("Contact info"), button:has-text("Contact info")').first();
    if (await contactBtn.count() === 0) {
      return res.status(404).json({ error: 'Could not find "Contact info" button' });
    }
    await contactBtn.click();
    await page.waitForTimeout(2000);

    // Click "Edit" or "Add website" in the dialog
    const editBtn = page.locator('div[role="dialog"] button:has-text("Edit"), div[role="dialog"] a:has-text("Edit")').first();
    if (await editBtn.isVisible().catch(() => false)) {
      await editBtn.click();
      await page.waitForTimeout(2000);
    }

    // Find the website input — it may be an input with a URL placeholder
    const websiteInput = page.locator('div[role="dialog"] input[type="url"], div[role="dialog"] input[placeholder*="Website"], div[role="dialog"] input[placeholder*="website"], div[role="dialog"] input[placeholder*="http"]').first();
    if (await websiteInput.count() === 0) {
      // Fallback: look for any input in the dialog that's not the name
      const inputs = await page.locator('div[role="dialog"] input:visible').all();
      for (const inp of inputs) {
        const ph = await inp.getAttribute('placeholder').catch(() => '');
        if (ph && (ph.includes('http') || ph.includes('url') || ph.includes('Website') || ph.includes('website'))) {
          await inp.fill(website);
          await clickSave();
          res.json({ status: 'ok', updated: ['website'] });
          return;
        }
      }
      return res.status(404).json({ error: 'Could not locate the website input in contact info' });
    }
    await websiteInput.fill(website);
    await clickSave();
    res.json({ status: 'ok', updated: ['website'] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Personal location ─────────────────────────────────────────────────

async function handleUpdateLocation(req, res) {
  const { location } = req.body;
  if (!location) return res.status(400).json({ error: 'location is required' });
  try {
    await ensureBrowser();
    await page.goto('https://www.linkedin.com/in/me/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    // Open the intro editor (same as headline)
    const editPencil = page.locator('button[aria-label*="Edit intro"], button[aria-label*="edit intro"]').first();
    const updatePrompt = page.getByText('Update headline', { exact: true }).first();

    if (await editPencil.count() > 0) {
      await editPencil.click();
    } else if (await updatePrompt.count() > 0) {
      const handle = await updatePrompt.evaluateHandle(
        "el => el.closest('button, a, [role=\\'button\\']') || el.parentElement"
      );
      await page.evaluate('el => el.click()', handle);
    } else {
      return res.status(404).json({ error: 'Could not locate the intro editor' });
    }
    await page.waitForTimeout(4000);

    // The location field is typically the last input in the intro dialog
    // It may have aria-label containing "location" or "Location"
    const allInputs = await page.locator("div[role='dialog'] input:visible, div[role='dialog'] textarea:visible").all();
    let locationInput = null;
    for (const inp of allInputs) {
      const label = await inp.getAttribute('aria-label').catch(() => null);
      if (label && label.toLowerCase().includes('location')) {
        locationInput = inp;
        break;
      }
    }
    // Fallback: the last input in the dialog
    if (!locationInput && allInputs.length > 0) {
      locationInput = allInputs[allInputs.length - 1];
    }
    if (!locationInput) {
      return res.status(404).json({ error: 'Could not locate the location input' });
    }
    await locationInput.fill(location);
    await clickSave();
    res.json({ status: 'ok', updated: ['location'] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Add experience entry ──────────────────────────────────────────────

async function handleAddExperience(req, res) {
  const { title, company, start_date, end_date, description, current } = req.body;
  if (!title || !company) {
    return res.status(400).json({ error: 'title and company are required' });
  }
  try {
    await ensureBrowser();
    await page.goto('https://www.linkedin.com/in/me/edit/experience/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    // Click "Add experience" button
    const addBtn = page.locator('button:has-text("Add experience"), button[aria-label*="Add experience"]').first();
    if (await addBtn.count() === 0) {
      return res.status(404).json({ error: 'Could not find "Add experience" button' });
    }
    await addBtn.click();
    await page.waitForTimeout(2000);

    // Fill the form
    const titleInput = page.locator('input[aria-label*="Title"], input[id*="title"], input[name*="title"]').first();
    if (await titleInput.count() > 0) {
      await titleInput.fill(title);
    }

    const companyInput = page.locator('input[aria-label*="Company"], input[aria-label*="company"], input[id*="company"], input[name*="company"]').first();
    if (await companyInput.count() > 0) {
      await companyInput.fill(company);
      await page.waitForTimeout(1000);
      // Click the first dropdown suggestion if available
      const suggestion = page.locator('[role="option"], [role="listbox"] div, .basic-typeahead__triggered-content div').first();
      if (await suggestion.isVisible().catch(() => false)) {
        await suggestion.click();
        await page.waitForTimeout(500);
      }
    }

    if (current) {
      const currentCheckbox = page.locator('input[type="checkbox"][id*="current"], input[type="checkbox"][aria-label*="current"], input[type="checkbox"][aria-label*="Current"]').first();
      if (await currentCheckbox.count() > 0) {
        const isChecked = await currentCheckbox.isChecked();
        if (!isChecked) await currentCheckbox.click();
      }
    }

    if (start_date) {
      const startInput = page.locator('input[aria-label*="Start"], input[id*="start"], input[name*="start"]').first();
      if (await startInput.count() > 0) {
        await startInput.fill(start_date);
      }
    }

    if (end_date && !current) {
      const endInput = page.locator('input[aria-label*="End"], input[id*="end"], input[name*="end"]').first();
      if (await endInput.count() > 0) {
        await endInput.fill(end_date);
      }
    }

    if (description) {
      const descInput = page.locator('textarea[aria-label*="Description"], textarea[id*="description"], textarea[name*="description"]').first();
      if (await descInput.count() > 0) {
        await descInput.fill(description);
      }
    }

    await clickSave();
    res.json({ status: 'ok', updated: ['experience'], entry: { title, company, start_date, end_date, current } });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Add education entry ───────────────────────────────────────────────

async function handleAddEducation(req, res) {
  const { school, degree, field_of_study, start_date, end_date, description } = req.body;
  if (!school) {
    return res.status(400).json({ error: 'school is required' });
  }
  try {
    await ensureBrowser();
    await page.goto('https://www.linkedin.com/in/me/edit/education/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    const addBtn = page.locator('button:has-text("Add education"), button[aria-label*="Add education"]').first();
    if (await addBtn.count() === 0) {
      return res.status(404).json({ error: 'Could not find "Add education" button' });
    }
    await addBtn.click();
    await page.waitForTimeout(2000);

    const schoolInput = page.locator('input[aria-label*="School"], input[aria-label*="school"], input[id*="school"], input[name*="school"]').first();
    if (await schoolInput.count() > 0) {
      await schoolInput.fill(school);
      await page.waitForTimeout(1000);
      const suggestion = page.locator('[role="option"], .basic-typeahead__triggered-content div').first();
      if (await suggestion.isVisible().catch(() => false)) {
        await suggestion.click();
        await page.waitForTimeout(500);
      }
    }

    if (degree) {
      const degreeInput = page.locator('input[aria-label*="Degree"], input[id*="degree"]').first();
      if (await degreeInput.count() > 0) await degreeInput.fill(degree);
    }

    if (field_of_study) {
      const fosInput = page.locator('input[aria-label*="field"], input[aria-label*="Field"], input[id*="field"]').first();
      if (await fosInput.count() > 0) await fosInput.fill(field_of_study);
    }

    if (start_date) {
      const startInput = page.locator('select[aria-label*="Start"], select[id*="start"]').first();
      if (await startInput.count() > 0) await startInput.selectOption(start_date);
    }

    if (end_date) {
      const endInput = page.locator('select[aria-label*="End"], select[id*="end"]').first();
      if (await endInput.count() > 0) await endInput.selectOption(end_date);
    }

    if (description) {
      const descInput = page.locator('textarea[aria-label*="Description"], textarea[id*="description"]').first();
      if (await descInput.count() > 0) await descInput.fill(description);
    }

    await clickSave();
    res.json({ status: 'ok', updated: ['education'], entry: { school, degree, field_of_study } });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Add skill ─────────────────────────────────────────────────────────

async function handleAddSkill(req, res) {
  const { skill } = req.body;
  if (!skill) return res.status(400).json({ error: 'skill is required' });
  try {
    await ensureBrowser();
    await page.goto('https://www.linkedin.com/in/me/edit/skills/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    // Click "Add a new skill" button
    const addBtn = page.locator('button:has-text("Add a new skill"), button[aria-label*="Add skill"], button:has-text("Add skill")').first();
    if (await addBtn.count() === 0) {
      return res.status(404).json({ error: 'Could not find "Add skill" button' });
    }
    await addBtn.click();
    await page.waitForTimeout(2000);

    const skillInput = page.locator('input[aria-label*="skill"], input[aria-label*="Skill"], input[placeholder*="skill"], input[placeholder*="Skill"]').first();
    if (await skillInput.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the skill input' });
    }
    await skillInput.fill(skill);
    await page.waitForTimeout(1000);

    // Click the first suggestion if available
    const suggestion = page.locator('[role="option"], .basic-typeahead__triggered-content div, [data-test-skill-option]').first();
    if (await suggestion.isVisible().catch(() => false)) {
      await suggestion.click();
      await page.waitForTimeout(500);
    }

    await clickSave();
    res.json({ status: 'ok', updated: ['skills'], skill });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Company page read ─────────────────────────────────────────────────

async function handleReadCompany(req, res) {
  const { vanity } = req.params;
  try {
    await ensureBrowser();
    await page.goto(`https://www.linkedin.com/company/${vanity}/about/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    const result = { vanity, url: page.url(), name: null, about: null, website: null, industry: null, specialties: null, logo_url: null, cover_url: null };

    // Name
    const nameEl = page.locator('h1, h2.org-top-card-summary__title').first();
    if (await nameEl.count() > 0) {
      result.name = (await nameEl.innerText()).trim();
    }

    // About / description
    const aboutEl = page.locator('p.org-about-us-organization-description__text, section:has(h2:has-text("About")) p, div[class*="organization-description"]').first();
    if (await aboutEl.count() > 0) {
      result.about = (await aboutEl.innerText()).trim();
    }

    // Website, industry, specialties from the details section
    const detailsDl = page.locator('dl.org-page-details__definition-list').first();
    if (await detailsDl.count() > 0) {
      const dtElements = await detailsDl.locator('dt').all();
      const ddElements = await detailsDl.locator('dd').all();
      for (let i = 0; i < Math.min(dtElements.length, ddElements.length); i++) {
        const label = (await dtElements[i].innerText()).trim().toLowerCase();
        const value = (await ddElements[i].innerText()).trim();
        if (label.includes('website')) result.website = value;
        if (label.includes('industry')) result.industry = value;
        if (label.includes('specialties')) result.specialties = value.split(',').map((s) => s.trim());
      }
    }

    // Logo
    const logoImg = page.locator('img.org-top-card-primary-content__logo, img[class*="company-logo"]').first();
    if (await logoImg.count() > 0) {
      result.logo_url = await logoImg.getAttribute('src');
    }

    // Cover
    const coverImg = page.locator('img.org-top-card-background-image, img[class*="cover"]').first();
    if (await coverImg.count() > 0) {
      result.cover_url = await coverImg.getAttribute('src');
    }

    res.json({ status: 'ok', company: result });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Company About ─────────────────────────────────────────────────────

async function handleUpdateCompanyAbout(req, res) {
  const { vanity } = req.params;
  const { about } = req.body;
  if (!about) return res.status(400).json({ error: 'about is required' });
  try {
    await ensureBrowser();
    await page.goto(`https://www.linkedin.com/company/${vanity}/about/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    // Click the "Edit" button in the About section
    const editBtn = page.locator('button[aria-label*="Edit about"], button[aria-label*="edit about"], button:has-text("Edit overview"), a:has-text("Edit overview"), button:has-text("Edit about")').first();
    if (await editBtn.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the company "Edit about" button' });
    }
    await editBtn.click();
    await page.waitForTimeout(3000);

    // Find the contenteditable or textarea editor
    const editor = page.locator('[contenteditable="true"], textarea:visible').first();
    if (await editor.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the company About editor' });
    }

    const isContentEditable = await editor.getAttribute('contenteditable').catch(() => null);
    if (isContentEditable === 'true') {
      await editor.click();
      await page.keyboard.press('Control+a');
      await page.keyboard.press('Delete');
      await page.keyboard.type(about);
    } else {
      await editor.fill(about);
    }

    await clickSave();
    res.json({ status: 'ok', updated: ['about'], vanity });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Company website ───────────────────────────────────────────────────

async function handleUpdateCompanyWebsite(req, res) {
  const { vanity } = req.params;
  const { website } = req.body;
  if (!website) return res.status(400).json({ error: 'website is required' });
  try {
    await ensureBrowser();
    await page.goto(`https://www.linkedin.com/company/${vanity}/about/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    // Click "Edit" in the details section
    const editBtn = page.locator('button[aria-label*="Edit details"], button:has-text("Edit details"), button[aria-label*="Edit info"]').first();
    if (await editBtn.count() === 0) {
      // Try the overview edit
      const overviewEdit = page.locator('button:has-text("Edit overview"), a:has-text("Edit overview")').first();
      if (await overviewEdit.count() > 0) {
        await overviewEdit.click();
        await page.waitForTimeout(3000);
      } else {
        return res.status(404).json({ error: 'Could not locate the company edit button' });
      }
    } else {
      await editBtn.click();
      await page.waitForTimeout(3000);
    }

    // Find the website input
    const websiteInput = page.locator('input[aria-label*="Website"], input[aria-label*="website"], input[placeholder*="Website"], input[placeholder*="website"], input[type="url"]').first();
    if (await websiteInput.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the website input' });
    }
    await websiteInput.fill(website);
    await clickSave();
    res.json({ status: 'ok', updated: ['website'], vanity });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Company specialties ───────────────────────────────────────────────

async function handleUpdateCompanySpecialties(req, res) {
  const { vanity } = req.params;
  const { specialties } = req.body;
  if (!specialties || !Array.isArray(specialties)) {
    return res.status(400).json({ error: 'specialties must be an array of strings' });
  }
  try {
    await ensureBrowser();
    await page.goto(`https://www.linkedin.com/company/${vanity}/about/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    const editBtn = page.locator('button[aria-label*="Edit details"], button:has-text("Edit details"), button[aria-label*="Edit info"], button:has-text("Edit overview")').first();
    if (await editBtn.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the company edit button' });
    }
    await editBtn.click();
    await page.waitForTimeout(3000);

    // Specialties input — usually a tag input or text input
    const specInput = page.locator('input[aria-label*="Specialties"], input[aria-label*="specialties"], input[placeholder*="Specialties"], input[placeholder*="specialties"]').first();
    if (await specInput.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the specialties input' });
    }

    for (const s of specialties) {
      await specInput.fill(s);
      await page.keyboard.press('Enter');
      await page.waitForTimeout(500);
    }

    await clickSave();
    res.json({ status: 'ok', updated: ['specialties'], vanity, specialties });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Company logo ──────────────────────────────────────────────────────

async function handleUploadCompanyLogo(req, res) {
  const { vanity } = req.params;
  const { image_base64, filename } = req.body;
  if (!image_base64) return res.status(400).json({ error: 'image_base64 is required' });
  try {
    await ensureBrowser();
    await page.goto(`https://www.linkedin.com/company/${vanity}/about/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    // Click the logo edit button
    const logoEdit = page.locator('button[aria-label*="logo"], button[aria-label*="Logo"], button[aria-label*="Edit logo"], .org-top-card-primary-content__logo button, button:has-text("Edit logo")').first();
    if (await logoEdit.count() === 0) {
      // Hover over the logo area
      const logoArea = page.locator('.org-top-card-primary-content__logo, img[class*="company-logo"]').first();
      if (await logoArea.count() > 0) {
        await logoArea.hover();
        await page.waitForTimeout(1000);
      }
    }
    if (await logoEdit.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the logo edit button' });
    }
    await logoEdit.click();
    await page.waitForTimeout(2000);

    const buffer = Buffer.from(image_base64, 'base64');
    const tmpPath = bufferToTempFile(buffer, filename || 'logo.jpg');
    try {
      const fileInput = page.locator('input[type="file"][accept*="image"]').first();
      await fileInput.setInputFiles(tmpPath);
      await page.waitForTimeout(3000);

      // Handle crop
      for (const btnText of ['Apply', 'Save', 'Done']) {
        const btn = page.locator(`button:has-text("${btnText}")`).first();
        if (await btn.isVisible().catch(() => false)) {
          await btn.click();
          await page.waitForTimeout(2000);
        }
      }
      res.json({ status: 'ok', updated: ['logo'], vanity });
    } finally {
      fs.unlinkSync(tmpPath);
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── API: Company cover ─────────────────────────────────────────────────────

async function handleUploadCompanyCover(req, res) {
  const { vanity } = req.params;
  const { image_base64, filename } = req.body;
  if (!image_base64) return res.status(400).json({ error: 'image_base64 is required' });
  try {
    await ensureBrowser();
    await page.goto(`https://www.linkedin.com/company/${vanity}/about/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle();

    const coverEdit = page.locator('button[aria-label*="cover"], button[aria-label*="Cover"], button[aria-label*="background"], button:has-text("Edit cover")').first();
    if (await coverEdit.count() === 0) {
      const coverArea = page.locator('.org-top-card-background, [class*="cover"]').first();
      if (await coverArea.count() > 0) {
        await coverArea.hover();
        await page.waitForTimeout(1000);
      }
    }
    if (await coverEdit.count() === 0) {
      return res.status(404).json({ error: 'Could not locate the cover edit button' });
    }
    await coverEdit.click();
    await page.waitForTimeout(2000);

    const buffer = Buffer.from(image_base64, 'base64');
    const tmpPath = bufferToTempFile(buffer, filename || 'cover.jpg');
    try {
      const fileInput = page.locator('input[type="file"][accept*="image"]').first();
      await fileInput.setInputFiles(tmpPath);
      await page.waitForTimeout(3000);

      for (const btnText of ['Apply', 'Save', 'Done', 'Next']) {
        const btn = page.locator(`button:has-text("${btnText}")`).first();
        if (await btn.isVisible().catch(() => false)) {
          await btn.click();
          await page.waitForTimeout(2000);
          break;
        }
      }
      await clickSave();
      res.json({ status: 'ok', updated: ['cover'], vanity });
    } finally {
      fs.unlinkSync(tmpPath);
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}

// ── Server setup ───────────────────────────────────────────────────────────

const app = express();
app.use(express.json({ limit: '50mb' }));

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'linkedin-browser-sidecar', has_session: !!storageState });
});

// Session
app.post('/session', handleSetSession);
app.get('/session', handleCheckSession);

// Personal profile
app.get('/profile', handleReadProfile);
app.post('/profile/headline', handleUpdateHeadline);
app.post('/profile/about', handleUpdateAbout);
app.post('/profile/cover', handleUploadCover);
app.post('/profile/picture', handleUploadPicture);
app.post('/profile/website', handleUpdateWebsite);
app.post('/profile/location', handleUpdateLocation);
app.post('/profile/experience', handleAddExperience);
app.post('/profile/education', handleAddEducation);
app.post('/profile/skills', handleAddSkill);

// Company page
app.get('/company/:vanity', handleReadCompany);
app.post('/company/:vanity/about', handleUpdateCompanyAbout);
app.post('/company/:vanity/website', handleUpdateCompanyWebsite);
app.post('/company/:vanity/specialties', handleUpdateCompanySpecialties);
app.post('/company/:vanity/logo', handleUploadCompanyLogo);
app.post('/company/:vanity/cover', handleUploadCompanyCover);

// Graceful shutdown
process.on('SIGTERM', async () => {
  await closeBrowser();
  process.exit(0);
});

app.listen(PORT, () => {
  console.log(`LinkedIn browser sidecar listening on port ${PORT}`);
});
