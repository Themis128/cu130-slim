const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  // 1. radios FIRST — last option per group (proven path to 1 issue)
  const groups = await page.locator('input[type=radio]:visible').evaluateAll(els => [...new Set(els.map(e => e.name))]);
  for (const n of groups) { try { await page.locator(`input[name="${n}"]:visible`).last().check({ force: true, timeout: 1500 }); } catch {} }
  // 2. text inputs in order (6 org fields)
  const texts = ['Baltzakis Themistoklis','https://cloudless.gr','Thermopilon 15','Koropi','Attica','19441'];
  const ins = await page.locator('input[type=text]:visible').all();
  for (let i = 0; i < Math.min(texts.length, ins.length); i++) {
    await ins[i].click(); await ins[i].pressSequentially(texts[i], { delay: 18 }); await page.waitForTimeout(120);
  }
  // 3. textareas in order (desc, detail, extra, testlogin, NA)
  const descs = [
    'SocialAuto — self-service SaaS for SMBs automating social-media management: publishes company posts, manages comments and engagement analytics on LinkedIn organization pages via Community Management API. https://social.cloudless.gr',
    'Page management: create and manage company posts, comments and reactions, monitor engagement. Page analytics: track post analytics and performance.',
    'Page management + Page analytics for the Cloudless organization page.',
    'Demo access: https://social.cloudless.gr — user: themistoklis@cloudless.gr — integrating CM API for the Cloudless org page. Product: https://cloudless.gr',
    'N/A — no personal data shared with other customers'
  ];
  const tas = await page.locator('textarea:visible').all();
  for (let i = 0; i < Math.min(descs.length, tas.length); i++) {
    await tas[i].click(); await tas[i].pressSequentially(descs[i], { delay: 8 }); await page.waitForTimeout(120);
  }
  // 4. recording URL = last text input
  const ins2 = await page.locator('input[type=text]:visible').all();
  if (ins2.length > 6) { await ins2[ins2.length-1].click(); await ins2[ins2.length-1].pressSequentially('https://social.cloudless.gr/api/v1/media/view?path=li-cm-demo.mp4', { delay: 10 }); }
  // 5. country + checkboxes
  try { await page.locator('select:visible').first().selectOption('Greece'); } catch {}
  for (const l of ['Facebook','Instagram','Twitter','Tiktok','Page management','Page analytics']) {
    try { await page.locator('label:visible').filter({ hasText: new RegExp('^\\s*' + l) }).first().check({ timeout: 1200 }); } catch {}
  }
  await page.locator('button:visible').filter({ hasText: /next/i }).first().click().catch(()=>{});
  await page.waitForTimeout(8000);
  const end = await page.locator('body').innerText();
  const iss = end.match(/(\d+) issues? to fix/) || end.match(/no issues|thank/i);
  console.log('issues:', iss ? iss[0] : 'none-found');
  console.log(end.slice(-300));
  await page.screenshot({ path: '/tmp/li_v2.png' });
  await browser.close();
})();
