const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: '/home/tbaltzakis/cu130-slim/.playwright-data/ms-browsers/chromium-1246/chrome-linux64/chrome' });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(5000);
  const texts = await page.locator('input[type=text]:visible, textarea:visible').all();
  console.log('fields:', texts.length);
  const vals = ['Cloudless','https://cloudless.gr','Thermopilon 15','Attica','19441'];
  for (let i = 0; i < Math.min(vals.length, texts.length); i++) {
    await texts[i].click();
    await texts[i].pressSequentially(vals[i], { delay: 45 });
    console.log('filled', i);
  }
  await page.screenshot({ path: '/tmp/li_p1.png' });
  console.log('radios:', await page.locator('input[type=radio]:visible').count());
  console.log('Q:', (await page.locator('.QuestionText').allTextContents()).length);
  await browser.close();
})();
