#!/usr/bin/env node
/* pdrive.js — Puppeteer driver for the :9229 debug Chrome (human-act lanes).
 *
 * Connects over CDP to the relay (default http://192.168.96.1:9229, the
 * per-IP bind WSL lanes use; override with CDP_URL). Uses puppeteer-core —
 * no bundled browser; drives the already-running debug Chrome.
 *
 * Why puppeteer over raw WS: page.click()/page.type() go through the CDP
 * Input domain = TRUSTED events. Meta/LinkedIn ignore el.click() and
 * synthetic events; Input.dispatchMouseEvent is what their React trees
 * accept (burned in from fb_recover_drive.py runs).
 *
 * Usage:
 *   node pdrive.js targets
 *   node pdrive.js open <url> [--match <substr>]
 *   node pdrive.js text [--match <substr>] [--n 2000]
 *   node pdrive.js eval <js> [--match <substr>]
 *   node pdrive.js click <text> [--match <substr>] [--exact]
 *   node pdrive.js fill <selector> <value> [--match <substr>]
 *   node pdrive.js type <selector> <text> [--match <substr>]   # real key events
 *   node pdrive.js shot <path.png> [--match <substr>]
 *   node pdrive.js run <script.js> [--match <substr>]
 *
 * --match picks the page whose URL contains the substring
 * (e.g. --match facebook.com). Default: first page target.
 * `run` loads a script exporting async (page, browser, ctx) => {}.
 */

const puppeteer = require('puppeteer-core');

const CDP_CANDIDATES = [
  process.env.CDP_URL,
  'http://192.168.96.1:9229',
  'http://localhost:9229',
  'http://127.0.0.1:9229',
].filter(Boolean);

function arg(flag, dflt) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : dflt;
}
function has(flag) { return process.argv.includes(flag); }
const MATCH = arg('--match', '');
const EXACT = has('--exact');

async function cdpBase() {
  for (const base of CDP_CANDIDATES) {
    try {
      const r = await fetch(base + '/json/version', { signal: AbortSignal.timeout(5000) });
      if (r.ok) return base;
    } catch (_) {}
  }
  throw new Error('no CDP endpoint — tried ' + CDP_CANDIDATES.join(', '));
}

async function connect() {
  const base = await cdpBase();
  const ver = await (await fetch(base + '/json/version')).json();
  const browser = await puppeteer.connect({
    browserWSEndpoint: ver.webSocketDebuggerUrl,
    defaultViewport: null,
  });
  return { browser, base };
}

function pickPage(browser) {
  const pages = browser.pages();
  return pages.then ? pages : Promise.resolve(pages);
}

async function getPage(browser) {
  const pages = await browser.pages();
  const pageTargets = pages.filter(p => p.url() && !p.url().startsWith('chrome'));
  if (MATCH) {
    const m = pageTargets.filter(p => p.url().includes(MATCH));
    // shortest URL first — the canonical tab, not deep flow/disavow tabs
    m.sort((a, b) => a.url().length - b.url().length);
    if (m.length) return m[0];
  }
  // prefer a real content page over about:blank
  return pageTargets.find(p => p.url() !== 'about:blank') || pageTargets[0] || pages[0];
}

async function withPage(fn) {
  const { browser } = await connect();
  try {
    const page = await getPage(browser);
    if (!page) throw new Error('no page targets');
    return await fn(page, browser);
  } finally {
    await browser.disconnect();
  }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function clickByText(page, text) {
  // Trusted-input click on the smallest element whose own text matches.
  const handle = await page.evaluateHandle((t, exact) => {
    const els = [...document.querySelectorAll(
      'div[role=button],button,a,label,[role=radio],[role=option],li,span'
    )].filter(e => {
      const label = e.getAttribute('aria-label') || '';
      const s = ((e.innerText || e.textContent || '') + ' ' + label).trim();
      return exact ? s === t || label === t : s.includes(t);
    });
    if (!els.length) return null;
    els.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
    return els[0];
  }, text, EXACT);
  const el = handle.asElement();
  if (!el) return false;
  await el.evaluate(e => e.scrollIntoView({ block: 'center' }));
  await sleep(300);
  await el.click();
  return true;
}

const VALUE_FLAGS = new Set(['--match', '--n']);

async function main() {
  const cmd = process.argv[2];
  const positional = [];
  for (let i = 3; i < process.argv.length; i++) {
    const a = process.argv[i];
    if (a.startsWith('--')) { if (VALUE_FLAGS.has(a)) i++; continue; }
    positional.push(a);
  }

  if (cmd === 'targets') {
    const { browser, base } = await connect();
    const pages = await browser.pages();
    for (const p of pages) console.log(p.url());
    await browser.disconnect();
    return;
  }

  if (cmd === 'open') {
    const url = positional[0];
    if (!url) throw new Error('open <url>');
    await withPage(async (page, browser) => {
      if (MATCH && page.url().includes(MATCH)) {
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      } else {
        const p = await browser.newPage();
        await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      }
      console.log('opened:', await (await getPage(browser)).url());
    });
    return;
  }

  if (cmd === 'text') {
    const n = parseInt(arg('--n', '2000'), 10);
    await withPage(async page => {
      console.log((await page.evaluate(() => document.body.innerText.slice(0, 200000))).slice(0, n));
    });
    return;
  }

  if (cmd === 'eval') {
    const js = positional.join(' ');
    await withPage(async page => {
      const r = await page.evaluate('(' + js + ')');
      console.log(typeof r === 'string' ? r : JSON.stringify(r));
    });
    return;
  }

  if (cmd === 'click') {
    const text = positional.join(' ');
    await withPage(async page => {
      const ok = await clickByText(page, text);
      console.log(ok ? 'clicked' : 'not-found');
      if (!ok) process.exitCode = 1;
    });
    return;
  }

  if (cmd === 'fill' || cmd === 'type') {
    const sel = positional[0];
    const val = positional.slice(1).join(' ');
    await withPage(async page => {
      const el = await page.$(sel);
      if (!el) { console.log('nf'); process.exitCode = 1; return; }
      await el.evaluate(e => e.scrollIntoView({ block: 'center' }));
      await el.click({ clickCount: 3 });
      if (cmd === 'type') await page.type(sel, val, { delay: 20 });
      else await el.evaluate((e, v) => {
        const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        s.call(e, v);
        e.dispatchEvent(new Event('input', { bubbles: true }));
        e.dispatchEvent(new Event('change', { bubbles: true }));
      }, val);
      console.log('done');
    });
    return;
  }

  if (cmd === 'shot') {
    const path = positional[0] || '/tmp/pdrive.png';
    await withPage(async page => {
      await page.screenshot({ path, fullPage: false });
      console.log('shot:', path);
    });
    return;
  }

  if (cmd === 'run') {
    const file = positional[0];
    if (!file) throw new Error('run <script.js>');
    const fn = require(require('path').resolve(file));
    await withPage(async (page, browser) => {
      const base = await cdpBase();
      await fn(page, browser, { cdpBase: base, match: MATCH });
    });
    return;
  }

  console.log('commands: targets|open|text|eval|click|fill|type|shot|run');
  process.exitCode = 2;
}

main().catch(e => { console.error('ERR', e.message); process.exit(1); });
