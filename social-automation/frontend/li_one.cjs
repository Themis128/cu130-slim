const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  // fill ALL fields — org data + descs + the 2 required bottom fields
  const plan = [
    ['Baltzakis Themistoklis'],['https://cloudless.gr'],['Thermopilon 15'],['Koropi'],['Attica'],['19441'],
    ['SocialAuto — self-service SaaS for SMBs automating social-media management: publishes company posts, manages comments and engagement analytics on LinkedIn organization pages via Community Management API. https://social.cloudless.gr'],
    ['Page management: create and manage company posts, comments and reactions, monitor engagement. Page analytics: track post analytics and performance.'],
    ['Page management and Page analytics for the Cloudless organization page.'],
    ['https://social.cloudless.gr/api/v1/media/view?path=li-cm-demo.mp4'],
    ['Demo access: https://social.cloudless.gr — user: themistoklis@cloudless.gr (credentials on request) — integrating Community Management API for the Cloudless org page. Product: https://cloudless.gr'],
    ['N/A — data not shared with other customers']
  ];
  const fields = await page.locator('input[type=text]:visible, textarea:visible').all();
  let pi = 0;
  for (const f of fields) {
    if (pi >= plan.length) break;
    const v = await f.inputValue().catch(() => '');
    if (v) continue;
    await f.click();
    await f.pressSequentially(plan[pi++][0], { delay: 10 });
    await page.waitForTimeout(150);
  }
  console.log('filled', pi, 'fields');
  const groups = await page.locator('input[type=radio]:visible').evaluateAll(els => [...new Set(els.map(e => e.name))]);
  for (const n of groups) { try { await page.locator(`input[name="${n}"]:visible`).last().check({ force: true, timeout: 1500 }); } catch {} }
  try { await page.locator('select:visible').first().selectOption('Greece'); } catch {}
  for (const l of ['Facebook','Instagram','Twitter','Tiktok','Page management','Page analytics']) {
    try { await page.locator('label:visible').filter({ hasText: new RegExp('^\\s*' + l) }).first().check({ timeout: 1200 }); } catch {}
  }
  await page.locator('button:visible').filter({ hasText: /next/i }).first().click().catch(()=>{});
  await page.waitForTimeout(8000);
  const end = await page.locator('body').innerText();
  console.log(end.slice(-400));
  await page.screenshot({ path: '/tmp/li_out.png' });
  await browser.close();
})();
