import { test, expect } from '@playwright/test';

test.describe('Analytics Page', () => {
  test('should load successfully and show overview metrics', async ({ page }) => {
    // Mock API requests — shapes match the real backend endpoints
    await page.route('**/api/v1/analytics/overview', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_posts: 10,
          published_posts: 6,
          scheduled_posts: 2,
          draft_posts: 1,
          failed_posts: 1,
          connected_accounts: 3,
          total_followers: 1250,
          total_engagement: 678,
        }),
      });
    });

    await page.route('**/api/v1/analytics/platforms', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            platform: 'linkedin',
            posts_count: 5,
            published_count: 4,
            scheduled_count: 1,
            total_engagement: 300,
            total_impressions: 5000,
            engagement_rate: 6.0,
          },
          {
            platform: 'facebook',
            posts_count: 5,
            published_count: 2,
            scheduled_count: 1,
            total_engagement: 378,
            total_impressions: 7345,
            engagement_rate: 5.1,
          },
        ]),
      });
    });

    await page.route('**/api/v1/analytics/top-posts', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            post_id: '1',
            content_text: 'This is a top performing post',
            platform: 'linkedin',
            impressions: 5000,
            engagement: 130,
            engagement_rate: 2.6,
            published_at: '2026-08-20T10:00:00Z',
          },
        ]),
      });
    });

    await page.route('**/api/v1/analytics/engagement', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { date: '2026-08-01', likes: 5, comments: 2, shares: 1, clicks: 2, total: 10 },
          { date: '2026-08-02', likes: 10, comments: 4, shares: 2, clicks: 4, total: 20 },
          { date: '2026-08-03', likes: 15, comments: 6, shares: 3, clicks: 6, total: 30 },
        ]),
      });
    });

    await page.route('**/api/v1/analytics/followers', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { platform: 'linkedin', followers: 1250, change: 0 },
        ]),
      });
    });

    await page.goto('/dashboard/analytics');

    // Wait for page to load — check for KPI cards
    await expect(page.getByText(/total engagement/i)).toBeVisible();
    await expect(page.getByText(/678/i)).toBeVisible();
    await expect(page.getByText(/posts published/i)).toBeVisible();
    await expect(page.getByText(/6/i)).toBeVisible();
    await expect(page.getByText(/connected accounts/i)).toBeVisible();
    await expect(page.getByText(/total followers/i)).toBeVisible();
    await expect(page.getByText(/1,250/i)).toBeVisible();

    // Check platform metrics
    await expect(page.getByText(/linkedin/i)).toBeVisible();
    await expect(page.getByText(/facebook/i)).toBeVisible();

    // Check top posts section
    await expect(page.getByText(/this is a top performing post/i)).toBeVisible();
  });

  test('should show loading state when data is fetching', async ({ page }) => {
    // Mock API requests to delay
    await page.route('**/api/v1/analytics/overview', async (route) => {
      return new Promise<void>((resolve) => {
        (window as any).resolveOverview = resolve;
      });
    });

    await page.route('**/api/v1/analytics/platforms', async (route) => {
      return new Promise<void>((resolve) => {
        (window as any).resolvePlatforms = resolve;
      });
    });

    await page.route('**/api/v1/analytics/top-posts', async (route) => {
      return new Promise<void>((resolve) => {
        (window as any).resolveTopPosts = resolve;
      });
    });

    await page.route('**/api/v1/analytics/engagement', async (route) => {
      return new Promise<void>((resolve) => {
        (window as any).resolveTrends = resolve;
      });
    });

    await page.route('**/api/v1/analytics/followers', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });

    await page.goto('/dashboard/analytics');

    // Check for loading skeletons or spinner
    await expect(page.getByText(/loading/i)).toBeVisible({ timeout: 5000 });

    // Resolve the requests
    (window as any).resolveOverview();
    (window as any).resolvePlatforms();
    (window as any).resolveTopPosts();
    (window as any).resolveTrends();
    await page.waitForTimeout(100);

    // After resolution, we should see the data
    await expect(page.getByText(/total engagement/i)).toBeVisible();
  });

  test('should display engagement chart data', async ({ page }) => {
    await page.route('**/api/v1/analytics/overview', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_posts: 10,
          published_posts: 6,
          scheduled_posts: 2,
          draft_posts: 1,
          failed_posts: 1,
          connected_accounts: 3,
          total_followers: 1250,
          total_engagement: 678,
        }),
      });
    });

    await page.route('**/api/v1/analytics/platforms', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            platform: 'linkedin',
            posts_count: 5,
            published_count: 4,
            scheduled_count: 1,
            total_engagement: 300,
            total_impressions: 5000,
            engagement_rate: 6.0,
          },
        ]),
      });
    });

    await page.route('**/api/v1/analytics/top-posts', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            post_id: '1',
            content_text: 'This is a top performing post',
            platform: 'linkedin',
            impressions: 5000,
            engagement: 130,
            engagement_rate: 2.6,
            published_at: '2026-08-20T10:00:00Z',
          },
        ]),
      });
    });

    await page.route('**/api/v1/analytics/engagement', async (route) => {
      const url = new URL(route.request().url());
      const days = parseInt(url.searchParams.get('days') || '30');
      const data = [];
      for (let i = 0; i < Math.min(days, 30); i++) {
        data.push({
          date: `2026-08-${(i % 30) + 1}`.padStart(10, '0'),
          likes: (i % 30) + 5,
          comments: (i % 10) + 1,
          shares: (i % 5) + 1,
          clicks: (i % 8) + 2,
          total: (i % 30) + 10,
        });
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(data),
      });
    });

    await page.route('**/api/v1/analytics/followers', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { platform: 'linkedin', followers: 1250, change: 0 },
        ]),
      });
    });

    await page.goto('/dashboard/analytics');

    // Wait for initial load
    await expect(page.getByText(/total engagement/i)).toBeVisible();

    // Check engagement chart is rendered
    await expect(page.getByText(/engagement over time/i)).toBeVisible();

    // Check top posts are visible
    await expect(page.getByText(/this is a top performing post/i)).toBeVisible();
  });
});
