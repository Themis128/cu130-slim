import { test, expect } from './helpers/auth';

test.describe('Settings sub-pages — real backend', () => {
  test('audit logs page renders heading', async ({ authenticatedPage: page }) => {
    await page.goto('/settings/audit-logs');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/settings\/audit-logs/);
    await expect(page.getByRole('heading', { name: /audit logs/i })).toBeVisible({ timeout: 15000 });
  });

  test('audit logs page shows filter buttons and entry count', async ({ authenticatedPage: page }) => {
    await page.goto('/settings/audit-logs');
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('heading', { name: /audit logs/i })).toBeVisible({ timeout: 15000 });
    // Filter buttons should be visible
    await expect(page.getByRole('button', { name: 'All' })).toBeVisible({ timeout: 10000 });
    // Should show entry count heading or empty state — use first() to avoid strict mode
    await expect(
      page.getByRole('heading', { name: /\d+ entries/i })
    ).toBeVisible({ timeout: 10000 });
  });

  test('AI usage page renders heading', async ({ authenticatedPage: page }) => {
    await page.goto('/settings/ai-providers/usage');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/settings\/ai-providers\/usage/);
    await expect(page.getByRole('heading', { name: /ai usage/i })).toBeVisible({ timeout: 15000 });
  });

  test('AI usage page shows back link to providers', async ({ authenticatedPage: page }) => {
    await page.goto('/settings/ai-providers/usage');
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('heading', { name: /ai usage/i })).toBeVisible({ timeout: 15000 });
    // Should have a back link to AI providers settings
    await expect(page.getByRole('link', { name: /back|ai providers|settings/i })).toBeVisible({ timeout: 10000 });
  });
});
