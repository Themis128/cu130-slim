import { test, expect } from './helpers/auth';

test.describe('Accounts Page — real backend', () => {
  test('should render the Channels heading', async ({ authenticatedPage: page }) => {
    await page.goto('/accounts');
    await expect(page).toHaveURL('/accounts');
    await expect(page.getByRole('heading', { name: 'Channels' })).toBeVisible({ timeout: 15000 });
  });

  test('should display all platform connect cards', async ({ authenticatedPage: page }) => {
    await page.goto('/accounts');
    // Each platform has an h3 heading
    for (const name of ['LinkedIn', 'Twitter / X', 'Instagram', 'Facebook', 'Messenger', 'Threads', 'TikTok', 'Telegram']) {
      await expect(page.getByRole('heading', { name, level: 3 })).toBeVisible({ timeout: 15000 });
    }
  });

  test('should show connect buttons for each platform', async ({ authenticatedPage: page }) => {
    await page.goto('/accounts');
    // Cards expose Personal / Business-Page connect buttons
    await expect(page.getByRole('button', { name: /^Personal$/i }).first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /business/i }).first()).toBeVisible();
    const personal = await page.getByRole('button', { name: /^Personal$/i }).count();
    expect(personal).toBeGreaterThanOrEqual(6);
  });

  test('should show a Connections button', async ({ authenticatedPage: page }) => {
    await page.goto('/accounts');
    await expect(page.getByRole('button', { name: /connections/i })).toBeVisible({ timeout: 15000 });
  });
});
