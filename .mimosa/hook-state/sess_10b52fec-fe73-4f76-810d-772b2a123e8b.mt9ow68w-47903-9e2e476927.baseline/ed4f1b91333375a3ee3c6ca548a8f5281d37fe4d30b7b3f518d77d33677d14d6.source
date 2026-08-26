import { test, expect } from '@playwright/test';

test.describe('Dashboard Page', () => {
  test('should load successfully and show dashboard content', async ({ page }) => {
    // Mock the API calls for the dashboard
    await page.route('**/api/overview-metrics', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_posts: 10,
          published_posts: 5,
          scheduled_posts: 3,
          connected_accounts: 2,
        }),
      });
    });

    await page.route('**/api/top-posts', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            post_id: '1',
            content: 'This is a test post',
            platform: 'twitter',
            likes: 10,
            comments: 5,
            shares: 2,
            published_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
          },
        ]),
      });
    });

    await page.route('**/api/scheduled-posts', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            post_id: '2',
            content: 'Scheduled post',
            platform: 'facebook',
            scheduled_for: new Date(Date.now() + 3600000).toISOString(),
            created_at: new Date().toISOString(),
          },
        ]),
      });
    });

    // Mock the advisor endpoint (if any)
    await page.route('**/api/advisor', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await page.goto('/dashboard');
    await expect(page).toHaveURL('/dashboard');

    // Check for the welcome header
    await expect(page.getByRole('heading', { name: /dashboard/i })).toBeVisible();
    await expect(page.getByText(/overview of your social media automation/i)).toBeVisible();

    // Check for the stats cards (we expect 4 cards)
    await expect(page.getByText(/total posts/i)).toBeVisible();
    await expect(page.getByText(/published/i)).toBeVisible();
    await expect(page.getByText(/scheduled/i)).toBeVisible();
    await expect(page.getByText(/connected accounts/i)).toBeVisible();

    // Check for the recent activity heading
    await expect(page.getByRole('heading', { name: /recent activity/i })).toBeVisible();

    // Check for the top posts heading
    await expect(page.getByRole('heading', { name: /top posts/i })).toBeVisible();

    // Check for the quick actions heading
    await expect(page.getByRole('heading', { name: /quick actions/i })).toBeVisible();

    // Check for at least one quick action button
    await expect(page.getByRole('link', { name: /create post/i })).toBeVisible();
  });
});
