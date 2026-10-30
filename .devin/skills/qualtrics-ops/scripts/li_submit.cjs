const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  // text fields in order — fill all visible text inputs + textareas by index
  const answers = [
    'Baltzakis Themistoklis (Cloudless)',  // legal name
    'https://cloudless.gr',                 // website
    'Thermopilon 15',                       // street
    'Koropi',                               // city
    'Attica',                               // state
    '19441',                                // zip
    'Greece'                                // country (if text)
  ];
  const tf = await page.locator('input[type=text]:visible').all();
  for (let i = 0; i < Math.min(answers.length, tf.length); i++) {
    await tf[i].click(); await tf[i].pressSequentially(answers[i], { delay: 30 });
    await page.waitForTimeout(300);
  }
  // country dropdown if present
  try { await page.locator('select:visible').first().selectOption({ label: 'Greece' }); } catch {}

  // radios — answer each question group by its context
  const radioQ = async (match, pick) => {
    const q = page.locator('.question-display, .QuestionText, div').filter({ hasText: match }).last();
    try { await q.locator('label').filter({ hasText: pick }).first().click({ timeout: 3000 }); } catch {}
  };
  await radioQ('another LinkedIn developer application', 'No');
  await radioQ('requesting access to the Community Management', 'new product/service');
  await radioQ('primary use case', 'Platform');
  await radioQ('customers based', 'Europe');
  await radioQ('How many clients', '1-50');
  await radioQ('shared with other customers', 'No');

  // checkboxes — the real use cases only
  const checkLabel = async (text) => {
    try { await page.locator('label:visible').filter({ hasText: text }).first().click({ timeout: 2000 }); } catch {}
  };
  await checkLabel('Page management');
  await checkLabel('Page analytics');

  // textareas — business description + use-case detail + test login + recording
  const tas = await page.locator('textarea:visible').all();
  const desc = [
    'SocialAuto is a self-service SaaS platform for SMBs that automates social-media management. It publishes to our own Cloudless organization page and, for clients, manages their LinkedIn pages — posts, comments, engagement analytics — via Community Management API. https://social.cloudless.gr',
    'Page management: publish and manage company posts, comments and reactions; Page analytics: track post performance and engagement metrics for the pages we manage.',
    'Demo access: https://social.cloudless.gr — reviewer login: themistoklis@cloudless.gr (credentials available on request). Product: https://cloudless.gr',
    'https://social.cloudless.gr/api/v1/media/view?path=li-cm-demo.mp4'
  ];
  for (let i = 0; i < Math.min(desc.length, tas.length); i++) {
    await tas[i].click(); await tas[i].pressSequentially(desc[i], { delay: 15 });
    await page.waitForTimeout(300);
  }
  // last text field might be the recording URL
  const rec = page.locator('input[type=text]:visible').last();
  try { await rec.click(); await rec.pressSequentially('https://social.cloudless.gr/api/v1/media/view?path=li-cm-demo.mp4', { delay: 10 }); } catch {}

  await page.screenshot({ path: '/tmp/li_review.png' });
  console.log('filled — review at /tmp/li_review.png');
  // Next/submit
  const nxt = page.locator('button, [role=button], input[type=submit]').filter({ hasText: /next|submit/i }).first();
  try { await nxt.click({ timeout: 4000 }); await page.waitForTimeout(4000); } catch {}
  console.log((await page.locator('body').innerText()).slice(-300));
  await page.screenshot({ path: '/tmp/li_after.png' });
  await browser.close();
})();
