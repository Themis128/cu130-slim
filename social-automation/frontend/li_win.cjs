const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  // text inputs (org fields) — 6 in order
  const texts = ['Baltzakis Themistoklis','https://cloudless.gr','Thermopilon 15','Koropi','Attica','19441'];
  const ins = await page.locator('input[type=text]:visible').all();
  for (let i = 0; i < Math.min(texts.length, ins.length); i++) {
    await ins[i].click(); await ins[i].pressSequentially(texts[i], { delay: 20 });
    await page.waitForTimeout(150);
  }
  // textareas — business-desc, usecase-detail, then the review fields
  const descs = [
    'SocialAuto — self-service SaaS platform for SMBs that automates social-media management. It publishes company posts, manages comments, and tracks engagement analytics on LinkedIn organization pages via the Community Management API. Product: https://cloudless.gr — app: https://social.cloudless.gr',
    'Page management: create and manage company posts, comments and reactions, monitor engagement. Page analytics: track post analytics and performance for the pages we manage.',
    'Page management + Page analytics for the Cloudless organization page.',
    'Demo access: https://social.cloudless.gr — user: themistoklis@cloudless.gr (credentials on request) — integrating Community Management API for the Cloudless org page.',
    'N/A — no personal data shared with other customers'
  ];
  const tas = await page.locator('textarea:visible').all();
  for (let i = 0; i < Math.min(descs.length, tas.length); i++) {
    await tas[i].click(); await tas[i].pressSequentially(descs[i], { delay: 8 });
    await page.waitForTimeout(150);
  }
  // recording URL — the LAST text input (after the org fields)
  const ins2 = await page.locator('input[type=text]:visible').all();
  if (ins2.length > 6) {
    await ins2[ins2.length - 1].click();
    await ins2[ins2.length - 1].pressSequentially('https://social.cloudless.gr/api/v1/media/view?path=li-cm-demo.mp4', { delay: 10 });
  }
  // radios — correct answers
  const picks = [['another LinkedIn developer application','No'],['requesting access','new product'],['primary use case','Platform'],['customers based','Europe'],['How many clients','1-50'],['shared with other customers','No']];
  for (const [m, c] of picks) {
    const q = page.locator('div').filter({ hasText: new RegExp(m) }).last();
    try { await q.locator('label').filter({ hasText: c }).first().click({ timeout: 2000 }); } catch {}
  }
  try { await page.locator('select:visible').first().selectOption('Greece'); } catch {}
  try { await page.locator('[role=combobox]:visible').first().click(); await page.locator('text=Greece').last().click(); } catch {}
  for (const l of ['Facebook','Instagram','Twitter','Tiktok','Page management','Page analytics']) {
    try { await page.locator('label:visible').filter({ hasText: new RegExp('^\\s*' + l) }).first().check({ timeout: 1200 }); } catch {}
  }
  console.log('all filled — Next');
  await page.locator('button:visible').filter({ hasText: /next/i }).first().click().catch(()=>{});
  await page.waitForTimeout(8000);
  const end = await page.locator('body').innerText();
  console.log(end.slice(-400));
  await page.screenshot({ path: '/tmp/li_end2.png' });
  await browser.close();
})();
