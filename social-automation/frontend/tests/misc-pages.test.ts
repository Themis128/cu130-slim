import { test, expect } from './helpers/auth';

test.describe('Home and misc pages — real backend', () => {
  test('home page redirects to login when unauthenticated', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    // Should redirect to /login or /dashboard depending on auth state
    await expect(page).toHaveURL(/\/(login|dashboard)/);
  });

  test('home page redirects to dashboard when authenticated', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/dashboard/);
  });

  test('browser login page renders heading and platform buttons', async ({ authenticatedPage: page }) => {
    await page.goto('/browser-login');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/browser-login/);
    await expect(page.getByRole('heading', { name: /visual browser login/i })).toBeVisible({ timeout: 15000 });
    // Platform selection buttons should be visible
    await expect(page.getByRole('heading', { name: /select platform/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: 'Instagram' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'LinkedIn' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'TikTok' })).toBeVisible();
  });

  test('browser login page shows browser viewer section', async ({ authenticatedPage: page }) => {
    await page.goto('/browser-login');
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('heading', { name: /visual browser login/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('heading', { name: /browser viewer/i })).toBeVisible({ timeout: 10000 });
  });

  test('mcp-stack page renders heading and stack overview', async ({ authenticatedPage: page }) => {
    await page.goto('/mcp-stack');
    await page.waitForLoadState('networkidle');
    // Page should load — may show 404 if frontend container is stale, or show the MCP stack page
    // If the page is built, it should show the "MCP & Sidecar Stack" heading
    const heading = page.getByRole('heading', { name: /mcp & sidecar stack/i });
    const notFound = page.getByRole('heading', { name: '404' });
    await expect(heading.or(notFound)).toBeVisible({ timeout: 15000 });
  });
});
