const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('https://linkedincustomerops.qualtrics.com/jfe/form/SV_3Q1SbLSZoImyw6O', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  // fill all text fields correctly by their question context — locate by nearby label text
  const fillNear = async (label, val) => {
    const q = page.locator('div').filter({ hasText: label }).last();
    const inp = q.locator('input[type=text], textarea').first();
    try {
      await inp.click(); await inp.fill('');
      await inp.pressSequentially(val, { delay: 20 });
      return 'ok';
    } catch { return 'miss'; }
  };
  console.log('legal:', await fillNear('Legal Name', 'Baltzakis Themistoklis'));
  console.log('website:', await fillNear('Organization Website', 'https://cloudless.gr'));
  console.log('street:', await fillNear('street address', 'Thermopilon 15'));
  console.log('state:', await fillNear('State/Province', 'Attica'));
  console.log('zip:', await fillNear('Zip/Postal', '19441'));
  console.log('recording:', await fillNear('screen recording', 'https://social.cloudless.gr/api/v1/media/view?path=li-cm-demo.mp4'));
  console.log('testlogin:', await fillNear('test login details', 'Demo access: https://social.cloudless.gr — user: themistoklis@cloudless.gr — integrating CM API for the Cloudless org page'));

  // radios + checkboxes
  const pick = async (match, choice) => {
    const q = page.locator('div').filter({ hasText: match }).last();
    try { await q.locator('label').filter({ hasText: choice }).first().click({ timeout: 2500 }); } catch {}
  };
  await pick('another LinkedIn developer application', 'No');
  await pick('requesting access', 'new product/service');
  await pick('primary use case', 'Platform');
  await pick('customers based', 'Europe');
  await pick('How many clients', '1-50');
  await pick('shared with other customers', 'No');
  // country dropdown
  try { await page.locator('select:visible').first().selectOption('Greece'); } catch {}
  try { await page.locator('[role=combobox]:visible').first().click(); await page.locator('text=Greece').first().click(); } catch {}
  // use-case checkboxes
  const chk = async (t) => { try { await page.locator('label:visible').filter({ hasText: t }).first().click({ timeout: 2000 }); } catch {} };
  await chk('Page management'); await chk('Page analytics');
  await chk('Facebook'); await chk('Instagram'); await chk('Twitter');

  await page.screenshot({ path: '/tmp/li_r2.png' });
  console.log('review2 saved');
  await browser.close();
})();
