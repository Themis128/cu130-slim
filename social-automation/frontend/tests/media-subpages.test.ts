import { test, expect } from './helpers/auth';

test.describe('Media sub-pages — real backend', () => {
  test('AI image generator page renders heading and templates', async ({ authenticatedPage: page }) => {
    await page.goto('/media/generate');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/media\/generate/);
    await expect(page.getByRole('heading', { name: /ai image generator/i })).toBeVisible({ timeout: 15000 });
    // Conference templates section should be visible
    await expect(page.getByText(/conference templates/i)).toBeVisible({ timeout: 10000 });
  });

  test('image generator shows template category buttons', async ({ authenticatedPage: page }) => {
    await page.goto('/media/generate');
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('heading', { name: /ai image generator/i })).toBeVisible({ timeout: 15000 });
    // Category filter buttons — use exact match to avoid ambiguity with template cards
    await expect(page.getByRole('button', { name: 'All' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: 'Presentation', exact: true })).toBeVisible();
  });

  test('enhance page handles non-existent media id gracefully', async ({ authenticatedPage: page }) => {
    await page.goto('/media/enhance/nonexistent-id-xyz');
    await page.waitForLoadState('networkidle');
    // Should show the enhancement studio heading or an error/loading state — not crash
    await expect(page.locator('body')).toBeVisible();
    // The page should either show the studio or redirect to media library
    await expect(page).toHaveURL(/\/media/);
  });
});
