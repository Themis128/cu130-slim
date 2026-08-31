import { test, expect } from './helpers/auth';

/**
 * Analytics Page — real backend.
 *
 * A fresh test user has zero data, so all metrics show 0 and the page
 * renders empty states. We verify the structural elements that always
 * render: the heading, KPI cards, chart sections, and filter controls.
 */

test.describe('Analytics Page — real backend', () => {
  test('should load and show the analytics heading', async ({ authenticatedPage: page }) => {
    await page.goto('/analytics');
    await expect(page).toHaveURL('/analytics');
    await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible();
    await expect(page.getByText(/track your social media performance/i)).toBeVisible();
  });

  test('should display all 5 KPI cards', async ({ authenticatedPage: page }) => {
    await page.goto('/analytics');
    // Wait for the loading skeleton to clear.
    // "Total Engagement" appears in multiple places (KPI card + chart labels),
    // so use exact match to target the KPI card label specifically.
    await expect(page.getByText('Total Engagement', { exact: true })).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('Posts Published', { exact: true })).toBeVisible();
    await expect(page.getByText('Avg Eng / Post', { exact: true })).toBeVisible();
    await expect(page.getByText('Connected Accounts', { exact: true })).toBeVisible();
    await expect(page.getByText('Total Followers', { exact: true })).toBeVisible();
  });

  test('should display the engagement over time chart section', async ({ authenticatedPage: page }) => {
    await page.goto('/analytics');
    await expect(page.getByText(/engagement over time/i)).toBeVisible({ timeout: 20000 });
  });

  test('should display the time range selector', async ({ authenticatedPage: page }) => {
    await page.goto('/analytics');
    // The time range select trigger shows "Last 30 days"
    await expect(page.getByText(/last 30 days/i)).toBeVisible({ timeout: 20000 });
  });

  test('should display the compare toggle button', async ({ authenticatedPage: page }) => {
    await page.goto('/analytics');
    await expect(page.getByRole('button', { name: /compare/i })).toBeVisible({ timeout: 20000 });
  });

  test('should display the sync button', async ({ authenticatedPage: page }) => {
    await page.goto('/analytics');
    await expect(page.getByRole('button', { name: /sync/i })).toBeVisible({ timeout: 20000 });
  });
});
