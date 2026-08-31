import { test, expect, TEST_USER } from './helpers/auth';

test.describe('Settings Page — real backend', () => {
  test('should render the Settings heading', async ({ authenticatedPage: page }) => {
    await page.goto('/settings');
    await expect(page).toHaveURL('/settings');
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible({ timeout: 15000 });
  });

  test('should display tabs including Profile, Security, and AI Providers', async ({ authenticatedPage: page }) => {
    await page.goto('/settings');
    await expect(page.getByRole('tab', { name: 'Profile' })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('tab', { name: 'Security' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'AI Providers' })).toBeVisible();
  });

  test('should show Profile Information heading on the Profile tab', async ({ authenticatedPage: page }) => {
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: /profile information/i })).toBeVisible({ timeout: 15000 });
  });

  test('should show Save Changes button', async ({ authenticatedPage: page }) => {
    await page.goto('/settings');
    await expect(page.getByRole('button', { name: /save changes/i })).toBeVisible({ timeout: 15000 });
  });

  test('should navigate to AI Providers page when clicking the tab', async ({ authenticatedPage: page }) => {
    await page.goto('/settings');
    await page.waitForLoadState('networkidle');
    await page.getByRole('tab', { name: 'AI Providers' }).click();
    await expect(page).toHaveURL(/\/settings\/ai-providers/, { timeout: 20000 });
  });

  test('should show Security content when clicking the Security tab', async ({ authenticatedPage: page }) => {
    await page.goto('/settings');
    await page.waitForLoadState('networkidle');
    await page.getByRole('tab', { name: 'Security' }).click();
    await expect(page.getByRole('heading', { name: /change password/i })).toBeVisible({ timeout: 20000 });
  });
});
