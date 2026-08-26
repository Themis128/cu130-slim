import { test, expect } from '@playwright/test';

test.describe('Analytics Page', () => {
  test('should load successfully and show overview metrics', async ({ page }) => {
    // Mock API requests
    await page.route('**/api/v1/analytics/overview', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_impressions: 12345,
          total_engagement: 678,
          engagement_rate: 5.5,
          total_posts: 10,
        }),
      });
    });

    await page.route('**/api/v1/analytics/platforms', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            platform: 'instagram',
            total_impressions: 5000,
            total_engagement: 300,
            engagement_rate: 6.0,
            published_count: 5,
            posts_count: 5,
          },
          {
            platform: 'facebook',
            total_impressions: 7345,
            total_engagement: 378,
            engagement_rate: 5.1,
            published_count: 5,
            posts_count: 5,
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
            content: 'This is a top performing post',
            platform: 'instagram',
            likes: 100,
            comments: 20,
            shares: 10,
            published_at: '2026-08-20T10:00:00Z',
            created_at: '2026-08-20T10:00:00Z',
          },
        ]),
      });
    });

    await page.route('**/api/v1/analytics/engagement-trends', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { date: '2026-08-01', value: 10 },
          { date: '2026-08-02', value: 20 },
          { date: '2026-08-03', value: 30 },
        ]),
      });
    });

    await page.goto('/dashboard/analytics');

    // Wait for page to load
    await expect(page.getByRole('heading', { name: /overview/i })).toBeVisible();
    await expect(page.getByText(/total impressions/i)).toBeVisible();
    await expect(page.getByText(/12,345/i)).toBeVisible();
    await expect(page.getByText(/total engagement/i)).toBeVisible();
    await expect(page.getByText(/678/i)).toBeVisible();
    await expect(page.getByText(/engagement rate/i)).toBeVisible();
    await expect(page.getByText(/5.5%/i)).toBeVisible();
    await expect(page.getByText(/total posts/i)).toBeVisible();
    await expect(page.getByText(/10/i)).toBeVisible();

    // Check platform metrics table
    await expect(page.getByText(/instagram/i)).toBeVisible();
    await expect(page.getByText(/5,000/i)).toBeVisible();
    await expect(page.getByText(/facebook/i)).toBeVisible();
    await expect(page.getByText(/7,345/i)).toBeVisible();

    // Check top posts section
    await expect(page.getByRole('heading', { name: /top performing posts/i })).toBeVisible();
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

    await page.route('**/api/v1/analytics/engagement-trends', async (route) => {
      return new Promise<void>((resolve) => {
        (window as any).resolveTrends = resolve;
      });
    });

    await page.goto('/dashboard/analytics');

    // Check for loading skeletons or text
    await expect(page.getByText(/loading/i)).toBeVisible();
    // Or check for skeleton elements
    await expect(page.getByRole('img', { name: /skeleton/i })).toBeVisible({ timeout: 5000 });

    // Resolve the requests
    (window as any).resolveOverview();
    (window as any).resolvePlatforms();
    (window as any).resolveTopPosts();
    (window as any).resolveTrends();
    await page.waitForTimeout(100);

    // After resolution, we should see the data
    await expect(page.getByText(/total impressions/i)).toBeVisible();
  });

  test('should filter by platform', async ({ page }) => {
    // Mock API requests
    await page.route('**/api/v1/analytics/overview', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_impressions: 12345,
          total_engagement: 678,
          engagement_rate: 5.5,
          total_posts: 10,
        }),
      });
    });

    await page.route('**/api/v1/analytics/platforms', async (route) => {
      // This endpoint might receive a platform filter query param
      const url = new URL(route.request().url());
      const platform = url.searchParams.get('platform') || '';
      let data = [
        {
          platform: 'instagram',
          total_impressions: 5000,
          total_engagement: 300,
          engagement_rate: 6.0,
          published_count: 5,
          posts_count: 5,
        },
        {
          platform: 'facebook',
          total_impressions: 7345,
          total_engagement: 378,
          engagement_rate: 5.1,
          published_count: 5,
          posts_count: 5,
        },
      ];
      if (platform) {
        data = data.filter((p) => p.platform === platform);
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(data),
      });
    });

    await page.route('**/api/v1/analytics/top-posts', async (route) => {
      const url = new URL(route.request().url());
      const platform = url.searchParams.get('platform') || undefined;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            post_id: '1',
            content: 'This is a top performing post',
            platform: platform || 'instagram',
            likes: 100,
            comments: 20,
            shares: 10,
            published_at: '2026-08-20T10:00:00Z',
            created_at: '2026-08-20T10:00:00Z',
          },
        ]),
      });
    });

    await page.route('**/api/v1/analytics/engagement-trends', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { date: '2026-08-01', value: 10 },
          { date: '2026-08-02', value: 20 },
          { date: '2026-08-03', value: 30 },
        ]),
      });
    });

    await page.goto('/dashboard/analytics');

    // Wait for initial load
    await expect(page.getByText(/total impressions/i)).toBeVisible();

    // Click on platform filter dropdown
    await page.getByRole('combobox', { name: /platform filter/i }).click();
    // Select Instagram
    await page.getByRole('option', { name: /instagram/i }).click();

    // Wait for platform metrics to update (should show only Instagram)
    await expect(page.getByText(/instagram/i)).toBeVisible();
    await expect(page.getByText(/facebook/i)).not.toBeVisible();
    await expect(page.getByText(/5,000/i)).toBeVisible();
    await expect(page.getByText(/7,345/i)).not.toBeVisible();

    // Check that top posts are filtered (if we had a platform param)
    // The top posts mock uses the platform from query, so it should show Instagram
    await expect(page.getByText(/this is a top performing post/i)).toBeVisible();
  });

  test('should toggle compare mode', async ({ page }) => {
    // Mock API requests
    await page.route('**/api/v1/analytics/overview', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_impressions: 12345,
          total_engagement: 678,
          engagement_rate: 5.5,
          total_posts: 10,
        }),
      });
    });

    await page.route('**/api/v1/analytics/platforms', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            platform: 'instagram',
            total_impressions: 5000,
            total_engagement: 300,
            engagement_rate: 6.0,
            published_count: 5,
            posts_count: 5,
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
            content: 'This is a top performing post',
            platform: 'instagram',
            likes: 100,
            comments: 20,
            shares: 10,
            published_at: '2026-08-20T10:00:00Z',
            created_at: '2026-08-20T10:00:00Z',
          },
        ]),
      });
    });

    // For engagement trends, when compare mode is on, it requests double the days
    await page.route('**/api/v1/analytics/engagement-trends', async (route) => {
      const url = new URL(route.request().url());
      const days = parseInt(url.searchParams.get('days') || '30');
      // If days is 60 (compare mode on), return 60 days of data
      const data = [];
      for (let i = 0; i < days; i++) {
        data.push({ date: `2026-08-${(i % 30) + 1}`, value: (i % 30) + 10 });
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(data),
      });
    });

    await page.goto('/dashboard/analytics');

    // Wait for initial load
    await expect(page.getByText(/total impressions/i)).toBeVisible();

    // Toggle compare mode
    await page.getByRole('checkbox', { name: /compare mode/i }).check();

    // Wait for the trend chart to update (should now show comparison)
    // We can check for some text that indicates comparison, or just wait for the request
    await page.waitForTimeout(500); // Wait for re-render

    // The page should still show the overview metrics
    await expect(page.getByText(/total impressions/i)).toBeVisible();
  });
});
