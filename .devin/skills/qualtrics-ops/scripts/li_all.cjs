const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  const t = await page.locator('input[type=text]:visible').all();
  const ta = await page.locator('textarea:visible').all();
  const texts = [
    'Baltzakis Themistoklis',           // legal name
    'https://cloudless.gr',             // website
    'Thermopilon 15',                   // street
    'Koropi',                           // city
    'Attica',                           // state/province
    '19441',                            // zip
    'https://social.cloudless.gr/api/v1/media/view?path=li-cm-demo.mp4'  // recording URL
  ];
  const tas = [
    'SocialAuto is a self-service SaaS platform for SMBs that automates social-media management — publishes company posts, manages comments, and tracks engagement analytics on LinkedIn organization pages via Community Management API. https://social.cloudless.gr',
    'Page management: create/manage company posts, comments, reactions, monitor engagement. Page analytics: track post analytics and performance.',
    'Demo access: https://social.cloudless.gr — user: themistoklis@cloudless.gr — integrating CM API for the Cloudless org page (product: https://cloudless.gr)',
    'N/A', 'N/A'
  ];
  for (let i = 0; i < Math.min(texts.length, t.length); i++) {
    await t[i].click(); await t[i].pressSequentially(texts[i], { delay: 25 }); await page.waitForTimeout(250);
  }
  for (let i = 0; i < Math.min(tas.length, ta.length); i++) {
    await ta[i].click(); await ta[i].pressSequentially(tas[i], { delay: 10 }); await page.waitForTimeout(250);
  }
  // radios — last option in each group ('No'/correct default per our answers)
  const groups = await page.locator('input[type=radio]:visible').evaluateAll(els => [...new Set(els.map(e => e.name))]);
  const order = ['No','new product','Platform','Page management','Europe','1-50','No'];
  for (const n of groups) {
    const opts = page.locator(`input[name="${n}"]:visible`);
    const cnt = await opts.count();
    try { await opts.nth(cnt - 1).check({ force: true, timeout: 2500 }); } catch {}
  }
  // country dropdown
  try { await page.locator('select:visible').first().selectOption('Greece'); } catch {}
  // checkboxes — only the true partnerships + use cases
  for (const lbl of ['Facebook','Instagram','Twitter','Tiktok','Page management','Page analytics']) {
    try { await page.locator('label:visible').filter({ hasText: new RegExp('^\\s*' + lbl) }).first().check({ timeout: 2000 }); } catch {}
  }
  await page.screenshot({ path: '/tmp/li_final2.png' });
  console.log('fields filled — attempting Next');
  try { await page.locator('button:visible').filter({ hasText: /next/i }).first().click({ timeout: 4000 }); } catch {}
  await page.waitForTimeout(5000);
  const end = (await page.locator('body').innerText()).slice(-250);
  console.log(end);
  await page.screenshot({ path: '/tmp/li_done.png' });
  await browser.close();
})();
