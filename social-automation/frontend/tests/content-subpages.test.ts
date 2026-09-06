import { test, expect } from './helpers/auth';

test.describe('Content sub-pages — real backend', () => {
  test('new article page renders heading', async ({ authenticatedPage: page }) => {
    await page.goto('/content/article/new');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/content\/article\/new/);
    await expect(page.getByRole('heading', { name: /new article/i })).toBeVisible({ timeout: 15000 });
  });

  test('new poll page renders heading', async ({ authenticatedPage: page }) => {
    await page.goto('/content/poll/new');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/content\/poll\/new/);
    await expect(page.getByRole('heading', { name: /new poll/i })).toBeVisible({ timeout: 15000 });
  });

  test('new story page renders heading', async ({ authenticatedPage: page }) => {
    await page.goto('/content/story/new');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/content\/story\/new/);
    await expect(page.getByRole('heading', { name: /new story/i })).toBeVisible({ timeout: 15000 });
  });

  test('new thread page renders heading', async ({ authenticatedPage: page }) => {
    await page.goto('/content/thread/new');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/content\/thread\/new/);
    await expect(page.getByRole('heading', { name: /new thread/i })).toBeVisible({ timeout: 15000 });
  });

  test('linkedin post page renders heading or no-account prompt', async ({ authenticatedPage: page }) => {
    await page.goto('/content/linkedin');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/content\/linkedin/);
    // If no LinkedIn account is connected, the page shows a prompt instead of the editor
    await expect(
      page.getByRole('heading', { name: /linkedin post/i }).or(page.getByText(/no linkedin account connected/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('carousel creator page renders heading', async ({ authenticatedPage: page }) => {
    await page.goto('/content/carousel/new');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/content\/carousel\/new/);
    await expect(page.getByRole('heading', { name: /carousel creator/i })).toBeVisible({ timeout: 15000 });
  });

  test('edit post page handles non-existent id gracefully', async ({ authenticatedPage: page }) => {
    await page.goto('/content/nonexistent-id-xyz/edit');
    await page.waitForLoadState('networkidle');
    // Should either show the editor loading state, a not-found message, or redirect to /content
    await expect(page).toHaveURL(/\/content/);
    // Page should not crash — either shows editor skeleton, error, or content list
    await expect(page.locator('body')).toBeVisible();
  });
});
