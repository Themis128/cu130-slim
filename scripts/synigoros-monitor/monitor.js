const { chromium } = require('playwright-core');
const fs = require('fs');
const https = require('https');
const path = require('path');

const DIR = __dirname;
const CFG = JSON.parse(fs.readFileSync(path.join(DIR, 'config.json'), 'utf8'));
const STATE_FILE = path.join(DIR, 'state.json');
const LOG = path.join(DIR, 'monitor.log');

const LOG_MAX_BYTES = 512 * 1024;

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  try {
    // rotate once the log outgrows 512 KiB — keeps one generation of history
    if (fs.statSync(LOG).size > LOG_MAX_BYTES) {
      fs.rmSync(LOG + '.1', { force: true });
      fs.renameSync(LOG, LOG + '.1');
    }
  } catch {}
  try { fs.appendFileSync(LOG, line + '\n'); } catch {}
  console.log(line);
}

function post(host, port, path, body, headers) {
  return new Promise((resolve) => {
    const req = (host === 'api.telegram.org' ? https : require('http')).request({
      hostname: host, port, path, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body), ...headers }
    }, (res) => { let d=''; res.on('data',c=>d+=c); res.on('end',()=>resolve({code:res.statusCode,body:d})); });
    req.on('error', () => resolve({code:0}));
    req.end(body);
  });
}

// ── Gmail early-check: the Ombudsman emails on every status change ────
// Near-instant lane — the page poll below is only the backstop.
function gmailAccessToken() {
  return new Promise((resolve) => {
    try {
      const dir = path.join(process.env.APPDATA || '', 'devin', 'secrets');
      const tok = JSON.parse(fs.readFileSync(path.join(dir, 'gmail-token.json'), 'utf8'));
      let c = {};
      try {
        const keys = JSON.parse(fs.readFileSync(path.join(dir, 'gcp-oauth.keys.json'), 'utf8'));
        c = keys.installed || keys.web || {};
      } catch {}
      if (!c.client_id) {
        // fallback: OAuth client creds live in the repo .env
        const env = fs.readFileSync(path.join(DIR, '..', '..', '.env'), 'utf8');
        const pick = (k) => (env.match(new RegExp('^' + k + '=(.+)$', 'm')) || [])[1];
        c = { client_id: (pick('GMAIL_OAUTH_CLIENT_ID') || '').trim().replace(/^["']|["']$/g, ''),
              client_secret: (pick('GMAIL_OAUTH_CLIENT_SECRET') || '').trim().replace(/^["']|["']$/g, '') };
      }
      const body = `client_id=${encodeURIComponent(c.client_id)}&client_secret=${encodeURIComponent(c.client_secret)}&refresh_token=${encodeURIComponent(tok.refresh_token)}&grant_type=refresh_token`;
      const req = https.request({ hostname: 'oauth2.googleapis.com', path: '/token', method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Content-Length': Buffer.byteLength(body) } },
        (res) => { let d = ''; res.on('data', x => d += x); res.on('end', () => { try { resolve(JSON.parse(d).access_token || null); } catch { resolve(null); } }); });
      req.on('error', () => resolve(null));
      req.end(body);
    } catch { resolve(null); }
  });
}

function gmailGet(at, p) {
  return new Promise((resolve) => {
    const req = https.request({ hostname: 'gmail.googleapis.com', path: p,
      headers: { Authorization: 'Bearer ' + at } },
      (res) => { let d = ''; res.on('data', x => d += x); res.on('end', () => { try { resolve(JSON.parse(d)); } catch { resolve({}); } }); });
    req.on('error', () => resolve({}));
    req.end();
  });
}

// Returns { mailId, newSubjects[] } — newest synigoros.gr message id plus
// subjects of messages not yet seen (empty when nothing new).
async function checkGmail(prevMailId) {
  const at = await gmailAccessToken();
  if (!at) return null;
  const list = await gmailGet(at, '/gmail/v1/users/me/messages?q=' + encodeURIComponent('from:synigoros.gr') + '&maxResults=5');
  const msgs = list.messages || [];
  if (!msgs.length) return { mailId: prevMailId, newSubjects: [] };
  const mailId = msgs[0].id;
  const newSubjects = [];
  if (prevMailId) {
    for (const m of msgs) {
      if (m.id === prevMailId) break;
      const d = await gmailGet(at, `/gmail/v1/users/me/messages/${m.id}?format=metadata&metadataHeaders=Subject`);
      const subj = ((d.payload || {}).headers || []).find(h => h.name === 'Subject');
      newSubjects.push((subj && subj.value) || '(no subject)');
    }
  }
  return { mailId, newSubjects };
}

// ── OTP lane: Synigoros emails a 6-digit login code after identify ────
// Each identify attempt sends a fresh code (5-min validity) — fetch the
// newest one from Gmail and feed it back into the page.
function b64urlDecode(s) {
  s = s.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  return Buffer.from(s, 'base64').toString('utf8');
}

function extractPlainBody(payload) {
  if (!payload) return '';
  const decode = (p) => p.body && p.body.data
    ? b64urlDecode(p.body.data) : '';
  // Synigoros mails are single-part text/html — collect plain first,
  // then html (tags stripped) so the OTP digits are still reachable.
  let html = '';
  const walk = (p) => {
    if (!p) return;
    if (p.mimeType === 'text/plain' && p.body && p.body.data)
      return decode(p);
    for (const c of p.parts || []) {
      const t = walk(c);
      if (t) return t;
    }
    if (p.mimeType === 'text/html' && p.body && p.body.data && !html)
      html = decode(p);
    return '';
  };
  const plain = walk(payload);
  if (plain) return plain;
  return html ? html.replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ') : '';
}

// Poll Gmail for the newest synigoros OTP; returns the 6-digit code or
// null. Synigoros throttles OTP sends — a rapid re-identify often gets NO
// new email, but the previous code is still valid for its 5-minute window,
// so accept anything younger than ~4.5 min rather than strictly post-attempt.
async function fetchSynigorosOtp(sinceMs, timeoutMs = 90000, notBeforeMs = 0) {
  const deadline = Date.now() + timeoutMs;
  const minInternal = () => Math.max(notBeforeMs, Math.min(sinceMs - 10000, Date.now() - 270000));
  while (Date.now() < deadline) {
    const at = await gmailAccessToken();
    if (!at) return null;
    const list = await gmailGet(at, '/gmail/v1/users/me/messages?q=' +
      encodeURIComponent('from:synigoros.gr newer_than:1h') + '&maxResults=5');
    for (const m of list.messages || []) {
      const d = await gmailGet(at, `/gmail/v1/users/me/messages/${m.id}?format=full`);
      // list items carry no internalDate — the full fetch does.
      if (d.internalDate && Number(d.internalDate) < minInternal()) continue;
      const headers = ((d.payload || {}).headers) || [];
      let subj = (headers.find(h => h.name === 'Subject') || {}).value || '';
      // Gmail returns UTF-8 subjects as encoded-words =?UTF-8?B?...?= —
      // decode before matching or the Greek subject never matches.
      subj = subj.replace(/=\?UTF-8\?B\?([^?]+)\?=/gi,
        (_, b) => Buffer.from(b, 'base64').toString('utf8'));
      const body = extractPlainBody(d.payload);
      const looksOtp = /Κωδικός|Επιβεβαίωσης|OTP/i.test(subj) ||
                       /κωδικός|επιβεβαίωσης/i.test(body);
      if (!looksOtp) continue;
      // prefer the digits right after «είναι:»/«κωδικός» — the boilerplate
      // footer carries other numbers (GDPR art. etc.)
      const code = (body.match(/(?:κωδικός|είναι)[^\d]{0,120}\b(\d{6})\b/i) ||
                    body.match(/^\s*(\d{6})\s*$/m) ||
                    body.match(/\b(\d{6})\b/))?.[1];
      if (code) return code;
    }
    await new Promise(r => setTimeout(r, 5000));
  }
  return null;
}

// Find a visible OTP input on the page (anything that isn't the identify
// form's webMail/webCode) and return a playwright selector for it.
async function findOtpSelector(page) {
  return page.evaluate(() => {
    const ins = [...document.querySelectorAll('input')].filter(i =>
      i.offsetParent !== null && i.id !== 'webMail' && i.id !== 'webCode' &&
      !i.disabled && (i.type === 'text' || i.type === 'tel' || i.type === 'number' || !i.type));
    if (!ins.length) return null;
    const i = ins[0];
    if (i.id) return '#' + CSS.escape(i.id);
    if (i.name) return `input[name="${CSS.escape(i.name)}"]`;
    return null;
  });
}

async function submitOtp(page) {
  // Enter usually submits OTP forms; fall back to a visible submit button.
  await page.keyboard.press('Enter');
  await page.waitForTimeout(800);
  const clicked = await page.evaluate(() => {
    const btn = [...document.querySelectorAll('button,input[type=submit]')]
      .find(b => b.offsetParent !== null &&
        /επιβεβα|συνέχεια|αποστολ|ok|επαλήθευ/i.test(b.innerText || b.value || ''));
    if (btn) { btn.click(); return true; }
    return false;
  });
  return clicked;
}

function botToken() {
  if (process.env.TELEGRAM_BOT_TOKEN) return process.env.TELEGRAM_BOT_TOKEN;
  try {
    const env = fs.readFileSync(path.join(DIR, '..', '..', '.env'), 'utf8');
    const m = env.match(/^TELEGRAM_BOT_TOKEN=(.+)$/m);
    return m && m[1].trim().replace(/^["']|["']$/g, '');
  } catch { return null; }
}

async function notify(text) {
  // Direct Bot API — channel-scoped, no social-api account auth needed.
  // (The old path via /api/v1/telegram/{accountId}/send 403s for non-owner
  // users — observed "Not authorized to manage this account".)
  const tok = botToken();
  if (tok) {
    const r = await post('api.telegram.org', 443, `/bot${tok}/sendMessage`,
      JSON.stringify({chat_id: CFG.chatId, text}));
    if (r.code === 200) return true;
    log(`bot-api send failed: ${r.code}`);
  }
  const login = await post('localhost', 8083, '/api/v1/auth/login',
    `username=${encodeURIComponent(CFG.adminEmail)}&password=${encodeURIComponent(CFG.adminPassword)}`,
    {'Content-Type':'application/x-www-form-urlencoded'});
  const tok2 = JSON.parse(login.body||'{}').access_token;
  if (!tok2) { log('auth failed'); return false; }
  const r = await post('localhost', 8083, `/api/v1/telegram/${CFG.accountId}/send`,
    JSON.stringify({chat_id: CFG.chatId, text}),
    {Authorization: `Bearer ${tok2}`});
  return r.code === 200;
}

(async () => {
  // Cheap Gmail check first — the Ombudsman emails on every status change,
  // so this relays changes minutes after they happen, without the browser.
  let prev = null;
  try { prev = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); } catch {}
  try {
    const g = await checkGmail(prev && prev.mailId);
    // Gmail lane is bookkeeping only (mailId dedupe + lastNews for the 3-day
    // follow-up rule) — notify ONLY on real status changes (2026-11-09):
    // OTP/form emails are noise, the 🔔 two-read confirmation covers changes.
    if (g && g.newSubjects.length) {
      log(`NEW MAIL (no notify): ${g.newSubjects.join(' | ')}`);
      prev = { ...(prev || {}), lastNews: new Date().toISOString() };
    }
    if (g) prev = { ...(prev || {}), mailId: g.mailId };
  } catch (e) { log('gmail check skip: ' + e.message); }

  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    await page.goto('https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas', { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForSelector('#webMail', { timeout: 20000 });
    await page.waitForTimeout(5000);
    await page.locator('#webMail').fill(CFG.email);
    await page.locator('#webCode').fill(CFG.kpa);
    await page.getByText('Ταυτοποίηση', { exact: true }).click({ force: true });
    // Synigoros now gates identify behind an emailed 6-digit OTP — the
    // status never renders until it's submitted. Poll for either outcome;
    // on an OTP prompt, fetch the fresh code from Gmail and feed it back.
    const identifyAt = Date.now();
    let otpAttempts = 0, lastOtpAt = -10, lastSubmitAt = 0;
    for (let i = 0; i < 40; i++) {
      await page.waitForTimeout(2000);
      const hasStatus = await page.evaluate(() => document.body.innerText.includes('Κατάσταση'));
      if (hasStatus) break;
      const otpSel = await findOtpSelector(page);
      // Retry the OTP up to 3×, ~20s apart — a rejected/stale code leaves
      // the prompt up; retries demand a NEWER email than the last submit.
      if (otpSel && otpAttempts < 3 && i - lastOtpAt >= 10) {
        otpAttempts++; lastOtpAt = i;
        log(`OTP prompt detected (attempt ${otpAttempts}) — fetching code`);
        const code = await fetchSynigorosOtp(identifyAt, 90000, lastSubmitAt);
        if (!code) { log('OTP fetch failed/timed out'); continue; }
        log('OTP acquired — submitting');
        await page.fill(otpSel, code);
        await submitOtp(page);
        lastSubmitAt = Date.now();
      }
    }
    // The info panel renders a template placeholder status first
    // («Αναμονή Επιβεβαίωσης Από Συνήγορο») and swaps in the real value
    // ~1s later — reading immediately captures the placeholder.
    await page.waitForTimeout(1500);

    const text = await page.evaluate(() => document.body.innerText);
    const m = [...text.matchAll(/Κατάσταση\s*\n\s*([^\n]+)/g)];
    const status = m.length ? m[m.length - 1][1].trim() : 'UNKNOWN';
    const updates = (text.match(/Ενημέρωση|Απάντηση|Σχόλιο|Έγγραφο/g) || []).length;

    // Two-read confirmation: Κατάσταση exists in multiple page blocks
    // (stepper vs detail) that race on render — raw reads flip-flop.
    // Alert only when the same new value repeats in consecutive polls.
    const confirmed = prev ? prev.status : status;
    if (!prev) {
      // Arm with a news baseline, otherwise the 3-day follow-up rule
      // (which keys off lastNews) can never fire on a fresh monitor.
      prev = { status, lastNews: new Date().toISOString() };
      await notify(`📋 Synigoros monitor armed.\nΚατάσταση: ${status}\nΠρωτόκολλο: ${CFG.kpa}`);
      log(`armed, status: ${status}`);
    } else if (status !== confirmed) {
      if (prev.candidate === status) {
        prev.lastNews = new Date().toISOString();
        await notify(`🔔 ΑΛΛΑΓΗ αναφοράς Συνηγόρου!\nΠριν: ${confirmed}\nΤώρα: ${status}\n\nΈλεγξε: https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas\nΚΠΑ: ${CFG.kpa}`);
        log(`CHANGE: ${confirmed} -> ${status}`);
        prev.status = status;
        prev.candidate = undefined;
      } else {
        prev.candidate = status;
        log(`candidate: ${confirmed} -> ${status} (needs one more matching poll)`);
      }
    } else {
      if (prev.candidate) prev.candidate = undefined;
      log(`no change: ${status}`);
    }
    // 3-day no-news rule → send the prepared follow-up once.
    if (prev && !prev.followupSent && prev.lastNews &&
        Date.now() - new Date(prev.lastNews).getTime() >= 3 * 24 * 3600e3) {
      try {
        require('child_process').execFileSync(
          'C:\\Program Files\\nodejs\\node.exe',
          [path.join(DIR, 'followup.js')], { stdio: 'inherit' });
        prev.followupSent = new Date().toISOString();
        log('follow-up sent (3d no news)');
      } catch (e) { log('followup failed: ' + e.message); }
    }
    // Recovery notice after an alerted outage streak, then clear it.
    if (prev && prev.errorAlerted) {
      await notify(`✅ Synigoros monitor recovered after ${prev.errorStreak} failed poll(s).\nΚατάσταση: ${status}`);
    }
    fs.writeFileSync(STATE_FILE, JSON.stringify({ status: prev ? prev.status : status, candidate: prev && prev.candidate, updates, mailId: prev && prev.mailId,
      lastNews: prev && prev.lastNews, followupSent: prev && prev.followupSent,
      errorStreak: 0, errorAlerted: false,
      checked: new Date().toISOString() }, null, 2));
    // Dated follow-up scheduler (ΔΥΠΑ/ΣΕΠΕ/Συνήγορος cadence) — fire-and-forget.
    try {
      require('child_process').spawn('C:\\Program Files\\nodejs\\node.exe',
        [path.join(DIR, 'admin-followups.js')], { stdio: 'ignore', detached: true }).unref();
    } catch (e) { log('admin-followups spawn failed: ' + e.message); }
  } catch (e) {
    log('ERROR: ' + e.message);
    // Debounce: synigoros.gr blips are ~3% of polls and self-heal — alert
    // only on the 2nd consecutive failure, once per outage.
    const streak = ((prev && prev.errorStreak) || 0) + 1;
    const alertNow = streak >= 2 && !(prev && prev.errorAlerted);
    try {
      fs.writeFileSync(STATE_FILE, JSON.stringify({
        status: prev && prev.status, candidate: prev && prev.candidate,
        updates: prev && prev.updates, mailId: prev && prev.mailId,
        lastNews: prev && prev.lastNews, followupSent: prev && prev.followupSent,
        errorStreak: streak, errorAlerted: alertNow || (prev && prev.errorAlerted) || false,
        lastError: String(e.message).slice(0, 300),
        checked: new Date().toISOString() }, null, 2));
    } catch {}
    if (alertNow) await notify(`⚠️ Synigoros monitor error (x${streak} consecutive): ${e.message}`);
  } finally {
    await browser.close();
  }
})();
