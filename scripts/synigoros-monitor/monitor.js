const { chromium } = require('playwright-core');
const fs = require('fs');
const https = require('https');
const path = require('path');

const DIR = __dirname;
const CFG = JSON.parse(fs.readFileSync(path.join(DIR, 'config.json'), 'utf8'));
const STATE_FILE = path.join(DIR, 'state.json');
const LOG = path.join(DIR, 'monitor.log');

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
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
      const keys = JSON.parse(fs.readFileSync(path.join(dir, 'gcp-oauth.keys.json'), 'utf8'));
      const c = keys.installed || keys.web || {};
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
    if (g && g.newSubjects.length) {
      await notify(`📨 Νέο email από Συνήγορο του Πολίτη!\n${g.newSubjects.map(s => '• ' + s).join('\n')}\n\nΗ κατάσταση της αναφοράς πιθανόν άλλαξε — έλεγξε το Gmail.`);
      log(`NEW MAIL: ${g.newSubjects.join(' | ')}`);
    }
    if (g) prev = { ...(prev || {}), mailId: g.mailId, lastNews: new Date().toISOString() };
  } catch (e) { log('gmail check skip: ' + e.message); }

  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    await page.goto('https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas', { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForSelector('#webMail', { timeout: 20000 });
    await page.waitForTimeout(5000);
    await page.locator('#webMail').click();
    await page.keyboard.type(CFG.email);
    await page.locator('#webCode').click();
    await page.keyboard.type(CFG.kpa);
    await page.getByText('Ταυτοποίηση', { exact: true }).click();
    await page.waitForFunction(() => document.body.innerText.includes('Κατάσταση'), { timeout: 20000 });

    const text = await page.evaluate(() => document.body.innerText);
    const m = text.match(/Κατάσταση\s*\n\s*([^\n]+)/);
    const status = m ? m[1].trim() : 'UNKNOWN';
    const updates = (text.match(/Ενημέρωση|Απάντηση|Σχόλιο|Έγγραφο/g) || []).length;

    if (!prev) {
      await notify(`📋 Synigoros monitor armed.\nΚατάσταση: ${status}\nΠρωτόκολλο: ${CFG.kpa}`);
      log(`armed, status: ${status}`);
    } else if (prev.status !== status) {
      prev.lastNews = new Date().toISOString();
      await notify(`🔔 ΑΛΛΑΓΗ αναφοράς Συνηγόρου!\nΠριν: ${prev.status}\nΤώρα: ${status}\n\nΈλεγξε: https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas\nΚΠΑ: ${CFG.kpa}`);
      log(`CHANGE: ${prev.status} -> ${status}`);
    } else {
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
    fs.writeFileSync(STATE_FILE, JSON.stringify({ status, updates, mailId: prev && prev.mailId,
      lastNews: prev && prev.lastNews, followupSent: prev && prev.followupSent,
      checked: new Date().toISOString() }, null, 2));
  } catch (e) {
    log('ERROR: ' + e.message);
    await notify(`⚠️ Synigoros monitor error: ${e.message}`);
  } finally {
    await browser.close();
  }
})();
