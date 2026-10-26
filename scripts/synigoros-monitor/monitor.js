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

async function notify(text) {
  const login = await post('localhost', 8083, '/api/v1/auth/login',
    `username=${encodeURIComponent(CFG.adminEmail)}&password=${encodeURIComponent(CFG.adminPassword)}`,
    {'Content-Type':'application/x-www-form-urlencoded'});
  const tok = JSON.parse(login.body||'{}').access_token;
  if (!tok) { log('auth failed'); return false; }
  const r = await post('localhost', 8083, `/api/v1/telegram/${CFG.accountId}/send`,
    JSON.stringify({chat_id: CFG.chatId, text}),
    {Authorization: `Bearer ${tok}`});
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
