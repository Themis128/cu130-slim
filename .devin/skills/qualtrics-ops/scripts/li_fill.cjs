const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  const fields = await page.locator('input[type=text]:visible, textarea:visible').all();
  console.log('fields:', fields.length);
  const vals = ['Cloudless','https://cloudless.gr','Thermopilon 15','Attica','19441'];
  for (let i = 0; i < Math.min(vals.length, fields.length); i++) {
    await fields[i].click();
    await fields[i].pressSequentially(vals[i], { delay: 45 });
  }
  // radios — click 'No' where it exists in each group
  const names = await page.locator('input[type=radio]:visible').evaluateAll(els => [...new Set(els.map(e => e.name))]);
  console.log('radio groups:', names.length);
  for (const n of names) {
    const opts = page.locator(`input[name="${n}"]`);
    const no = page.locator(`input[name="${n}"] ~ label, label:has(input[name="${n}"])`);
    try { await page.locator(`input[name="${n}"][value*="No"], input[name="${n}"] >> nth=-1`).first().click({ force: true, timeout: 3000 }); } catch {}
  }
  const boxes = await page.locator('input[type=checkbox]:visible').all();
  for (const b of boxes) { try { await b.check({ force: true, timeout: 2000 }); } catch {} }
  await page.screenshot({ path: '/tmp/li_final.png', fullPage: false });
  console.log('done — screenshot at /tmp/li_final.png');
  await browser.close();
})();
