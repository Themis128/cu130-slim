const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  const vis = await page.locator('input[type=text]:visible').all();
  console.log('text inputs:', vis.length, 'values:', (await Promise.all(vis.map(e=>e.inputValue().catch(()=>'')))).map(v=>v.slice(0,15)));
  const tas = await page.locator('textarea:visible').all();
  console.log('textareas:', tas.length);
  // label text → fill the input inside the same row container
  const rows = await page.locator('[role="listitem"], .QuestionOuter, tr, .choice').count();
  console.log('row-ish:', rows);
  await browser.close();
})();
