const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  // find the two required fields by their question text, fill the input inside their container
  const targets = [
    { label: 'screen recording', val: 'https://social.cloudless.gr/api/v1/media/view?path=li-cm-demo.mp4' },
    { label: 'test login details', val: 'Demo: https://social.cloudless.gr — user themistoklis@cloudless.gr (creds on request) — CM API for the Cloudless org page. Product: https://cloudless.gr' }
  ];
  for (const t of targets) {
    const q = page.locator('div').filter({ hasText: new RegExp(t.label) }).last();
    const inp = q.locator('input[type=text], textarea').first();
    try {
      await inp.click();
      await inp.fill('');
      await inp.pressSequentially(t.val, { delay: 12 });
      console.log('filled:', t.label);
    } catch (e) { console.log('miss:', t.label); }
  }
  // now do the whole form fill quickly (fresh session)
  const texts = ['Baltzakis Themistoklis','https://cloudless.gr','Thermopilon 15','Koropi','Attica','19441'];
  const inputs = await page.locator('input[type=text]:visible').all();
  for (let i = 0; i < Math.min(6, inputs.length); i++) {
    const v = await inputs[i].inputValue();
    if (!v) { await inputs[i].click(); await inputs[i].pressSequentially(texts[i], { delay: 18 }); }
  }
  const areas = ['SocialAuto — self-service SaaS for SMBs automating social-media management: publishes company posts, manages comments and engagement analytics on LinkedIn organization pages via Community Management API. https://social.cloudless.gr',
    'Page management: create and manage company posts, comments and reactions, monitor engagement. Page analytics: track post analytics and performance.'];
  const tas = await page.locator('textarea:visible').all();
  for (let i = 0; i < Math.min(areas.length, tas.length); i++) {
    const v = await tas[i].inputValue();
    if (!v) { await tas[i].click(); await tas[i].pressSequentially(areas[i], { delay: 8 }); }
  }
  const groups = await page.locator('input[type=radio]:visible').evaluateAll(els => [...new Set(els.map(e => e.name))]);
  for (const n of groups) { try { await page.locator(`input[name="${n}"]:visible`).last().check({ force: true, timeout: 1500 }); } catch {} }
  try { await page.locator('select:visible').first().selectOption('Greece'); } catch {}
  for (const l of ['Facebook','Instagram','Twitter','Tiktok','Page management','Page analytics']) {
    try { await page.locator('label:visible').filter({ hasText: new RegExp('^\\s*' + l) }).first().check({ timeout: 1500 }); } catch {}
  }
  console.log('filled — Next');
  await page.locator('button:visible').filter({ hasText: /next/i }).first().click().catch(()=>{});
  await page.waitForTimeout(7000);
  const end = await page.locator('body').innerText();
  console.log(end.slice(-300));
  await page.screenshot({ path: '/tmp/li_sub.png' });
  await browser.close();
})();
