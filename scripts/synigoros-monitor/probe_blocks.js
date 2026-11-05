// one-off probe: dump every Κατάσταση block + status words on the live page
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');
const CFG = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8'));

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    await page.goto('https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas', { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForSelector('#webMail', { timeout: 20000 });
    await page.waitForTimeout(5000);
    await page.locator('#webMail').fill(CFG.email);
    await page.locator('#webCode').fill(CFG.kpa);
    await page.getByText('Ταυτοποίηση', { exact: true }).click({ force: true });
    await page.waitForFunction(() => document.body.innerText.includes('Κατάσταση'), undefined, { timeout: 25000 });
    await page.waitForTimeout(1500);

    const out = await page.evaluate(() => {
      const t = document.body.innerText;
      const re = /Κατάσταση\s*\n\s*([^\n]+)/g;
      const idxs = []; let m;
      while ((m = re.exec(t))) {
        idxs.push({ at: m.index, value: m[1].trim(), ctx: t.slice(Math.max(0, m.index - 100), m.index + 140) });
      }
      const els = [...document.querySelectorAll('*')]
        .filter(e => e.children.length === 0 && e.textContent.trim() === 'Κατάσταση')
        .map(e => ({
          tag: e.tagName, cls: String(e.className).slice(0, 80),
          parentText: (e.parentElement ? e.parentElement.innerText.slice(0, 220) : ''),
          visible: !!(e.offsetWidth || e.offsetHeight),
        }));
      const words = (t.match(/Αναμονή[^\n]*|Προς Αποδοχή[^\n]*|Ολοκληρώθηκε[^\n]*|Επεξεργασία[^\n]*|Σε εξέλιξη[^\n]*/g) || []).slice(0, 12);
      const kpa = (t.match(/Κωδικός Παρακολούθησης Αναφοράς\s*\n?\s*(\d+)/) || [])[1] || '';
      const proto = (t.match(/Αριθμός Πρωτοκόλλου\s*\n?\s*(\d+)/) || [])[1] || '';
      return { kpa, proto, idxs, els, words };
    });
    console.log(JSON.stringify(out, null, 1));
  } finally {
    await browser.close();
  }
})();
