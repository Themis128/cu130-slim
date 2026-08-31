import { test, expect } from './helpers/auth';

test.describe('Media Library Page — real backend', () => {
  test('should render the Media Library heading', async ({ authenticatedPage: page }) => {
    await page.goto('/media');
    await expect(page).toHaveURL('/media');
    // Use exact match — there's also "Your media library is empty" heading
    await expect(page.getByRole('heading', { name: 'Media Library' }).first()).toBeVisible({ timeout: 15000 });
  });

  test('should show an upload button', async ({ authenticatedPage: page }) => {
    await page.goto('/media');
    await expect(page.getByRole('button', { name: 'Upload', exact: true })).toBeVisible({ timeout: 15000 });
  });

  test('should show an AI Generate button', async ({ authenticatedPage: page }) => {
    await page.goto('/media');
    await expect(page.getByRole('button', { name: 'AI Generate' })).toBeVisible({ timeout: 15000 });
  });

  test('should show search input', async ({ authenticatedPage: page }) => {
    await page.goto('/media');
    await expect(page.getByPlaceholder('Search media...')).toBeVisible({ timeout: 15000 });
  });

  test('should show empty state for a fresh user', async ({ authenticatedPage: page }) => {
    await page.goto('/media');
    await expect(page.getByRole('heading', { name: /your media library is empty/i })).toBeVisible({ timeout: 15000 });
  });
});
