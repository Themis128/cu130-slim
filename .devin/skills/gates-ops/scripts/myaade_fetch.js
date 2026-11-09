// myaade_fetch.js — drive myAADE via Playwright-over-CDP on debug Chrome :9222
// Downloads Βεβαίωση Τρέχουσας Εικόνας + Ιστορικό Μεταβολών to ./aade_out/
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const USER = process.env.TAXISNET_USER, PASS = process.env.TAXISNET_PASS;
  if (!USER || !PASS) { console.log('NO_CREDS'); process.exit(2); }
  fs.mkdirSync('aade_out', { recursive: true });
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const ctx = browser.contexts()[0] || await browser.newContext();
  const page = await ctx.newPage();
  const dump = async (tag) => {
    const inputs = await page.$$eval('input', els => els.map(e => ({n:e.name,t:e.type,id:e.id,ph:e.placeholder||''})).slice(0,15));
    const links = await page.$$eval('a,button', els => els.map(e => (e.innerText||'').trim()).filter(Boolean).slice(0,20));
    console.log(`@${tag} url=${page.url().slice(0,80)}`);
    console.log('inputs:', JSON.stringify(inputs));
    console.log('links:', JSON.stringify(links.slice(0,14)));
  };
  try {
    await page.goto('https://login.gsis.gr/oauth2server/gov/oauth/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(6000);
    await dump('landing');
    // SSO login — fill whatever credential fields exist
    const filled = await page.evaluate(({u,p}) => {
      const ins = [...document.querySelectorAll('input')];
      let done = [];
      for (const i of ins) {
        const nm=(i.name+i.id+i.placeholder).toLowerCase();
        if (/user|afm|αφμ|login|name/.test(nm) && i.type!=='password' && i.type!=='hidden') { i.value=u; i.dispatchEvent(new Event('input',{bubbles:true})); done.push('user@'+(i.name||i.id)); }
        if (i.type==='password') { i.value=p; i.dispatchEvent(new Event('input',{bubbles:true})); done.push('pass@'+(i.name||i.id)); }
      }
      return done;
    }, {u:USER,p:PASS});
    console.log('filled:', JSON.stringify(filled));
    if (filled.length) {
      await page.keyboard.press('Enter'); await page.waitForTimeout(8000); await dump('post-login');
    }
  } catch (e) { console.log('ERR', e.message.slice(0,200)); await dump('error'); }
  await page.close();
  await browser.close().catch(()=>{});
})().catch(e => { console.log('FATAL', e.message.slice(0,200)); process.exit(1); });
