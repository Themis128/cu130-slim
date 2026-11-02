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
  fs.appendFileSync(LOG, line + '\n');
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

    let prev = null;
    try { prev = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); } catch {}

    if (!prev) {
      await notify(`📋 Synigoros monitor armed.\nΚατάσταση: ${status}\nΠρωτόκολλο: ${CFG.kpa}`);
      log(`armed, status: ${status}`);
    } else if (prev.status !== status) {
      await notify(`🔔 ΑΛΛΑΓΗ αναφοράς Συνηγόρου!\nΠριν: ${prev.status}\nΤώρα: ${status}\n\nΈλεγξε: https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas\nΚΠΑ: ${CFG.kpa}`);
      log(`CHANGE: ${prev.status} -> ${status}`);
    } else {
      log(`no change: ${status}`);
    }
    fs.writeFileSync(STATE_FILE, JSON.stringify({ status, updates, checked: new Date().toISOString() }, null, 2));
  } catch (e) {
    log('ERROR: ' + e.message);
    await notify(`⚠️ Synigoros monitor error: ${e.message}`);
  } finally {
    await browser.close();
  }
})();
