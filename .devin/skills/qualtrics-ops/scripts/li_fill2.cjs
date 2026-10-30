const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  const labels = await page.locator('.QuestionText').allTextContents();
  console.log('questions:', labels.length);
  for (const l of labels) console.log(' -', l.replace(/\s+/g,' ').slice(0,90));
  await browser.close();
})();
