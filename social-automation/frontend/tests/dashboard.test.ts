import { test, expect } from './helpers/auth';

test.describe('Dashboard Page — real backend', () => {
  test('should render dashboard with real metrics for authenticated user', async ({ authenticatedPage: page }) => {
    await page.goto('/dashboard');
    await expect(page).toHaveURL('/dashboard');

    // Greeting header (time-based: Good morning/afternoon/evening)
    await expect(page.getByRole('heading', { name: /good (morning|afternoon|evening)/i })).toBeVisible();
    await expect(page.getByText(/social media overview for the last 30 days/i)).toBeVisible();

    // Stat cards (Published / Scheduled / Drafts / Failed) — real zeros for a fresh test user
    await expect(page.getByText('Published')).toBeVisible();
    await expect(page.getByText('Scheduled')).toBeVisible();
    await expect(page.getByText('Drafts')).toBeVisible();
    await expect(page.getByText('Failed')).toBeVisible();

    // Week calendar + best-time-to-post cards
    await expect(page.getByRole('heading', { name: /this week/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /best time to post/i })).toBeVisible();

    // Top posts + quick actions
    await expect(page.getByRole('heading', { name: /top performing posts/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /quick actions/i })).toBeVisible();

    // Quick action links
    await expect(page.getByRole('link', { name: /create post/i }).first()).toBeVisible();
    await expect(page.getByRole('link', { name: /open calendar/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /upload media/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /connect accounts/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /view analytics/i })).toBeVisible();
  });

  test('should show empty state for top posts on a fresh account', async ({ authenticatedPage: page }) => {
    await page.goto('/dashboard');
    // Fresh test user has zero published posts
    await expect(page.getByText(/no published posts yet/i)).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('link', { name: /create a post/i })).toBeVisible();
  });

  test('should redirect unauthenticated users to /login', async ({ page }) => {
    // Clear any tokens first
    await page.goto('/login');
    await page.evaluate(() => {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
    });
    await page.goto('/dashboard');
    // Dashboard layout redirects to /login when not authenticated
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
  });
});
