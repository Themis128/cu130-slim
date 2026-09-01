const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    recordVideo: {
      dir: '/workspace/docs/linkedin-api-demo/video',
      size: { width: 1280, height: 720 }
    }
  });
  const page = await context.newPage();

  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const BASE = 'http://localhost:8082';

  // Step 1: Login
  console.log('[1/11] Login');
  await page.goto(`${BASE}/login`);
  await sleep(2000);
  await page.getByRole('textbox', { name: 'Email' }).fill('tbaltzakis@cloudless.gr');
  await sleep(500);
  await page.getByRole('textbox', { name: 'Password' }).fill('TH!123789th!');
  await sleep(1000);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL('**/dashboard');
  await sleep(3000);

  // Step 2: Dashboard - let viewer see it
  console.log('[2/11] Dashboard');
  await sleep(5000);

  // Step 3: Accounts - scroll slowly
  console.log('[3/11] Accounts');
  await page.goto(`${BASE}/accounts`);
  await sleep(3000);
  for (let i = 0; i < 4; i++) {
    await page.mouse.wheel(0, 250);
    await sleep(1500);
  }
  await sleep(2000);
  await page.mouse.wheel(0, -1000);
  await sleep(2000);

  // Step 4: LinkedIn content page
  console.log('[4/11] LinkedIn content page');
  await page.goto(`${BASE}/content/linkedin`);
  await sleep(4000);

  // Step 5: Post editor
  console.log('[5/11] Post editor');
  await page.goto(`${BASE}/content/new`);
  await sleep(3000);

  // Select LinkedIn
  await page.getByRole('button', { name: 'in LinkedIn' }).click();
  await sleep(2000);

  // Step 6: Type content slowly
  console.log('[6/11] Writing LinkedIn post');
  await page.getByRole('textbox', { name: 'What do you want to share?' }).click();
  await sleep(1000);
  const postText = 'Excited to share how Cloudless helps startups ship faster with serverless cloud architecture! Clear skies, zero friction. Learn more at https://cloudless.gr #serverless #cloud #cloudflare';
  for (const ch of postText) {
    await page.keyboard.type(ch, { delay: 25 });
  }
  await sleep(3000);

  // Step 7: Save draft
  console.log('[7/11] Save draft');
  await page.getByRole('button', { name: 'Save Draft' }).click();
  await sleep(3000);

  // Step 8: Calendar
  console.log('[8/11] Calendar');
  await page.goto(`${BASE}/calendar`);
  await sleep(4000);

  // Step 9: Analytics
  console.log('[9/11] Analytics');
  await page.goto(`${BASE}/analytics`);
  await sleep(3000);
  for (let i = 0; i < 2; i++) {
    await page.mouse.wheel(0, 300);
    await sleep(1500);
  }
  await sleep(2000);

  // Step 10: Brand
  console.log('[10/11] Brand');
  await page.goto(`${BASE}/brand`);
  await sleep(4000);

  // Step 11: Settings
  console.log('[11/11] Settings');
  await page.goto(`${BASE}/settings`);
  await sleep(4000);

  // Close to finalize video
  await page.close();
  await context.close();
  await browser.close();

  console.log('Video recording complete!');
})();
