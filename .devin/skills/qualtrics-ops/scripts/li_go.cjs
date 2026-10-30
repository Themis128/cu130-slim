const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  const all = await page.locator('input[type=text]:visible, textarea:visible').all();
  const txt = ['Baltzakis Themistoklis','https://cloudless.gr','Thermopilon 15','Koropi','Attica','19441'];
  const areas = [
    'SocialAuto — self-service SaaS for SMBs: automates social-media management, publishes company posts, manages comments and engagement analytics on LinkedIn organization pages via Community Management API. https://social.cloudless.gr',
    'Page management: create and manage company posts, comments and reactions, monitor engagement. Page analytics: track post analytics and performance.',
    'Page management + Page analytics for the Cloudless organization page.',
    'https://social.cloudless.gr/api/v1/media/view?path=li-cm-demo.mp4',
    'Demo access: https://social.cloudless.gr — user: themistoklis@cloudless.gr (credentials on request) — integrating Community Management API for the Cloudless org page. Product: https://cloudless.gr'
  ];
  // text inputs are indexes 0,1,3,4,5,9,10 → the first 6 get the org fields, 9/10 get nothing or extra
  let ti = 0, ai = 0;
  for (let i = 0; i < all.length; i++) {
    const tag = await all[i].evaluate(e => e.tagName);
    if (tag === 'INPUT' && ti < txt.length) { await all[i].click(); await all[i].pressSequentially(txt[ti++], { delay: 20 }); }
    else if (tag === 'TEXTAREA' && ai < areas.length) { await all[i].click(); await all[i].pressSequentially(areas[ai++], { delay: 8 }); }
    await page.waitForTimeout(200);
  }
  // radios — correct picks by group order
  const groups = await page.locator('input[type=radio]:visible').evaluateAll(els => [...new Set(els.map(e => e.name))]);
  for (const n of groups) {
    const opts = page.locator(`input[name="${n}"]:visible`);
    try { await opts.last().check({ force: true, timeout: 2000 }); } catch {}
  }
  try { await page.locator('select:visible').first().selectOption('Greece'); } catch {}
  for (const l of ['Facebook','Instagram','Twitter','Tiktok','Page management','Page analytics']) {
    try { await page.locator('label:visible').filter({ hasText: new RegExp('^\\s*' + l) }).first().check({ timeout: 1500 }); } catch {}
  }
  console.log('filled all');
  await page.locator('button:visible').filter({ hasText: /next/i }).first().click().catch(()=>{});
  await page.waitForTimeout(6000);
  const end = (await page.locator('body').innerText());
  console.log(end.slice(-350));
  await page.screenshot({ path: '/tmp/li_end.png' });
  await browser.close();
})();
