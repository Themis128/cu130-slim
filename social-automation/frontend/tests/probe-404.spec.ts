import { test } from './helpers/auth';
test('probe 404s on brand assets', async ({ authenticatedPage: page }) => {
  page.on('response', (res) => {
    if (res.status() >= 400) console.log('BADREQ', res.status(), res.url());
  });
  await page.goto('/brand/assets');
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(2500);
});
