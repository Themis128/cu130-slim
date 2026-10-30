const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  // map every visible input/textarea to its preceding question label
  const fields = await page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll('input[type=text], textarea')) {
      if (!el.offsetParent) continue;
      let q = el; let label = '';
      for (let d = 0; d < 12 && q; d++) {
        const t = q.querySelector && (q.querySelector('.QuestionText') || q.querySelector('label'));
        if (t && t.textContent.trim().length > 10) { label = t.textContent.trim().slice(0, 50); break; }
        q = q.parentElement;
      }
      out.push({ i: out.length, label, tag: el.tagName });
    }
    return out;
  });
  for (const f of fields) console.log(f.i, f.tag, f.label.slice(0, 55));
  await browser.close();
})();
