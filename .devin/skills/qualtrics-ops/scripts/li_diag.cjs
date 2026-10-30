const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  // radios + fields (the proven fill)
  const groups = await page.locator('input[type=radio]:visible').evaluateAll(els => [...new Set(els.map(e => e.name))]);
  for (const n of groups) { try { await page.locator(`input[name="${n}"]:visible`).last().check({ force: true, timeout: 1500 }); } catch {} }
  const texts = ['Baltzakis Themistoklis','https://cloudless.gr','Thermopilon 15','Koropi','Attica','19441'];
  const ins = await page.locator('input[type=text]:visible').all();
  for (let i = 0; i < Math.min(texts.length, ins.length); i++) { await ins[i].click(); await ins[i].pressSequentially(texts[i], { delay: 18 }); await page.waitForTimeout(120); }
  const descs = ['SocialAuto — self-service SaaS for SMBs automating social-media management: publishes company posts, manages comments and engagement analytics on LinkedIn org pages via Community Management API. https://social.cloudless.gr','Page management + Page analytics for the Cloudless organization page.','Page management: posts/comments/reactions + engagement monitoring. Page analytics: performance tracking.','Demo: https://social.cloudless.gr — themistoklis@cloudless.gr — CM API for the Cloudless org page.','N/A'];
  const tas = await page.locator('textarea:visible').all();
  for (let i = 0; i < Math.min(descs.length, tas.length); i++) { await tas[i].click(); await tas[i].pressSequentially(descs[i], { delay: 8 }); await page.waitForTimeout(120); }
  const ins2 = await page.locator('input[type=text]:visible').all();
  if (ins2.length > 6) { await ins2[ins2.length-1].click(); await ins2[ins2.length-1].pressSequentially('https://social.cloudless.gr/api/v1/media/view?path=li-cm-demo.mp4', { delay: 10 }); }
  try { await page.locator('select:visible').first().selectOption('Greece'); } catch {}
  for (const l of ['Facebook','Instagram','Twitter','Tiktok','Page management','Page analytics']) { try { await page.locator('label:visible').filter({ hasText: new RegExp('^\\s*'+l) }).first().check({ timeout: 1200 }); } catch {} }
  await page.locator('button:visible').filter({ hasText: /next/i }).first().click().catch(()=>{});
  await page.waitForTimeout(4000);
  // click Go to first issue + identify the flagged field
  try { await page.locator('text=Go to first issue').first().click({ timeout: 3000 }); } catch {}
  await page.waitForTimeout(2000);
  const flagged = await page.locator('.ValidationError, [class*=Error], [aria-invalid]').evaluateAll(els => els.map(e => (e.querySelector('.QuestionText,label')||e).textContent.trim().slice(0,80))).catch(()=>[]);
  console.log('flagged:', flagged.slice(0,4));
  await page.screenshot({ path: '/tmp/li_flag.png' });
  await browser.close();
})();
