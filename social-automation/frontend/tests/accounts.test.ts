import { test, expect } from './helpers/auth';

test.describe('Accounts Page — real backend', () => {
  test('should render the Connected Accounts heading', async ({ authenticatedPage: page }) => {
    await page.goto('/accounts');
    await expect(page).toHaveURL('/accounts');
    await expect(page.getByRole('heading', { name: 'Connected Accounts' })).toBeVisible({ timeout: 15000 });
  });

  test('should display all six platform connect cards', async ({ authenticatedPage: page }) => {
    await page.goto('/accounts');
    // Each platform has an h3 heading
    await expect(page.getByRole('heading', { name: 'LinkedIn', level: 3 })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('heading', { name: 'Twitter / X', level: 3 })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Instagram', level: 3 })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Facebook', level: 3 })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Threads', level: 3 })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'TikTok', level: 3 })).toBeVisible();
  });

  test('should show Connect buttons for each platform', async ({ authenticatedPage: page }) => {
    await page.goto('/accounts');
    await expect(page.getByRole('button', { name: /Connect LinkedIn/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /Connect Twitter/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Connect Instagram/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Connect Facebook/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Connect Threads/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Connect TikTok/i })).toBeVisible();
  });

  test('should show a Connections button', async ({ authenticatedPage: page }) => {
    await page.goto('/accounts');
    await expect(page.getByRole('button', { name: /connections/i })).toBeVisible({ timeout: 15000 });
  });
});
