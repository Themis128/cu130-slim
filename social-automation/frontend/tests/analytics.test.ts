import { test, expect } from '@playwright/test';

test.describe('Analytics Page', () => {
  test.beforeEach(async ({ page }) => {
    // Mock authentication
    await page.goto('/dashboard');
    
    // Mock all the analytics API endpoints
    await page.route('**/api/overview-metrics', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          connected_accounts: 5,
          total_engagement: 12500,
          total_followers: 45000,
          published_posts: 120,
        }),
      });
    });

    await page.route('**/api/platform-metrics', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            platform: 'twitter',
            total_impressions: 50000,
            total_engagement: 5000,
            engagement_rate: 10.0,
            published_count: 50,
            posts_count: 60,
          },
          {
            platform: 'linkedin',
            total_impressions: 30000,
            total_engagement: 4500,
            engagement_rate: 15.0,
            published_count: 40,
            posts_count: 45,
          },
          {
            platform: 'instagram',
            total_impressions: 40000,
            total_engagement: 3000,
            engagement_rate: 7.5,
            published_count: 30,
            posts_count: 35,
          },
        ]),
      });
    });

    await page.route('**/api/top-posts', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            post_id: '1',
            content: 'This is a top performing post about our latest product launch',
            platform: 'twitter',
            likes: 150,
            comments: 25,
            shares: 10,
            published_at: new Date(Date.now() - 86400000).toISOString(),
            created_at: new Date(Date.now() - 86400000).toISOString(),
          },
          {
            post_id: '2',
            content: 'Another great post with high engagement',
            platform: 'linkedin',
            likes: 120,
            comments: 30,
            shares: 5,
            published_at: new Date(Date.now() - 172800000).toISOString(),
            created_at: new Date(Date.now() - 172800000).toISOString(),
          },
        ]),
      });
    });

    await page.route('**/api/engagement-trends', async (route) => {
      const days = 30;
      const data = Array.from({ length: days }, (_, i) => ({
        date: new Date(Date.now() - (days - 1 - i) * 86400000).toISOString().split('T')[0],
        value: Math.floor(Math.random() * 500) + 100,
      }));
      
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(data),
      });
    });
  });

  test('should load analytics page successfully', async ({ page }) => {
    await page.goto('/analytics');
    await expect(page).toHaveURL('/analytics');
    
    // Check for main heading
    await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible();
    await expect(page.getByText('Track your social media performance')).toBeVisible();
  });

  test('should display key metrics cards', async ({ page }) => {
    await page.goto('/analytics');
    
    // Check for all 4 metric cards
    await expect(page.getByText('Connected Accounts')).toBeVisible();
    await expect(page.getByText('Total Engagement')).toBeVisible();
    await expect(page.getByText('Total Followers')).toBeVisible();
    await expect(page.getByText('Posts Published')).toBeVisible();
    
    // Check that values are displayed
    await expect(page.getByText('5')).toBeVisible(); // Connected accounts
    await expect(page.getByText('12,500')).toBeVisible(); // Total engagement
    await expect(page.getByText('45,000')).toBeVisible(); // Total followers
    await expect(page.getByText('120')).toBeVisible(); // Published posts
  });

  test('should allow changing time range', async ({ page }) => {
    await page.goto('/analytics');
    
    // Find and click the time range selector
    const timeRangeSelect = page.getByRole('combobox').first();
    await timeRangeSelect.click();
    
    // Select different time range
    await page.getByRole('option', { name: 'Last 7 days' }).click();
    
    // Verify the selection changed
    await expect(timeRangeSelect).toHaveValue('7');
  });

  test('should allow filtering by platform', async ({ page }) => {
    await page.goto('/analytics');
    
    // Find the platform filter (second select)
    const platformSelect = page.locator('select').nth(1);
    await platformSelect.click();
    
    // Select specific platform
    await page.getByRole('option', { name: 'Twitter/X' }).click();
    
    // Verify the selection
    await expect(platformSelect).toHaveValue('twitter');
  });

  test('should toggle compare mode', async ({ page }) => {
    await page.goto('/analytics');
    
    // Find the compare button
    const compareButton = page.getByRole('button', { name: /compare periods/i });
    await expect(compareButton).toBeVisible();
    
    // Click to enable compare mode
    await compareButton.click();
    
    // Verify compare mode is active (button text changes)
    await expect(page.getByRole('button', { name: /comparing periods/i })).toBeVisible();
    
    // Verify engagement delta is shown
    await expect(page.getByText(/vs prev/i)).toBeVisible();
    
    // Click to disable compare mode
    await page.getByRole('button', { name: /comparing periods/i }).click();
    await expect(page.getByRole('button', { name: /compare periods/i })).toBeVisible();
  });

  test('should display engagement over time chart', async ({ page }) => {
    await page.goto('/analytics');
    
    // Check for the chart card
    await expect(page.getByRole('heading', { name: 'Engagement Over Time' })).toBeVisible();
    await expect(page.getByText('Daily engagement across all platforms')).toBeVisible();
    
    // Check that the chart area is rendered (ResponsiveContainer)
    const chartArea = page.locator('.recharts-responsive-container');
    await expect(chartArea).toBeVisible();
  });

  test('should display platform performance chart', async ({ page }) => {
    await page.goto('/analytics');
    
    // Check for the platform performance card
    await expect(page.getByRole('heading', { name: 'Platform Performance' })).toBeVisible();
    await expect(page.getByText('Engagement by platform')).toBeVisible();
    
    // Check that platform names are displayed in the chart
    await expect(page.getByText('twitter')).toBeVisible();
    await expect(page.getByText('linkedin')).toBeVisible();
    await expect(page.getByText('instagram')).toBeVisible();
  });

  test('should display impressions by platform chart', async ({ page }) => {
    await page.goto('/analytics');
    
    // Check for the impressions card
    await expect(page.getByRole('heading', { name: 'Impressions by Platform' })).toBeVisible();
    await expect(page.getByText('Total impressions per platform for the selected period')).toBeVisible();
  });

  test('should display platform breakdown table', async ({ page }) => {
    await page.goto('/analytics');
    
    // Check for the platform breakdown card
    await expect(page.getByRole('heading', { name: 'Platform Breakdown' })).toBeVisible();
    await expect(page.getByText('Detailed metrics per platform')).toBeVisible();
    
    // Check for table headers
    await expect(page.getByText('Platform')).toBeVisible();
    await expect(page.getByText('Impressions')).toBeVisible();
    await expect(page.getByText('Engagement')).toBeVisible();
    await expect(page.getByText('Eng. Rate')).toBeVisible();
    await expect(page.getByText('Published')).toBeVisible();
    await expect(page.getByText('Total Posts')).toBeVisible();
    
    // Check for data rows
    await expect(page.getByText('twitter')).toBeVisible();
    await expect(page.getByText('linkedin')).toBeVisible();
    await expect(page.getByText('instagram')).toBeVisible();
  });

  test('should display top performing posts', async ({ page }) => {
    await page.goto('/analytics');
    
    // Check for the top posts card
    await expect(page.getByRole('heading', { name: 'Top Performing Posts' })).toBeVisible();
    await expect(page.getByText('Your best content by engagement')).toBeVisible();
    
    // Check for post entries
    await expect(page.getByText('#1')).toBeVisible();
    await expect(page.getByText('#2')).toBeVisible();
    
    // Check for post content snippets
    await expect(page.getByText(/top performing post/i)).toBeVisible();
  });

  test('should show empty state when no top posts data', async ({ page }) => {
    // Mock empty top posts response
    await page.route('**/api/top-posts', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });
    
    await page.goto('/analytics');
    
    // Check for empty state
    await expect(page.getByText('No data for this period')).toBeVisible();
    await expect(page.getByText('Publish posts to start seeing engagement metrics')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Create a post' })).toBeVisible();
  });

  test('should show loading state while fetching data', async ({ page }) => {
    // Delay the API response to test loading state
    await page.route('**/api/overview-metrics', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 100));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          connected_accounts: 5,
          total_engagement: 12500,
          total_followers: 45000,
          published_posts: 120,
        }),
      });
    });
    
    await page.goto('/analytics');
    
    // Check for skeleton loaders (they should be present briefly)
    const skeletons = page.locator('.animate-pulse');
    await expect(skeletons.first()).toBeVisible();
  });

  test('should handle export button click', async ({ page }) => {
    await page.goto('/analytics');
    
    // Find and click the export button
    const exportButton = page.getByRole('button', { name: /export/i });
    await expect(exportButton).toBeVisible();
    await exportButton.click();
    
    // Verify toast notification appears
    await expect(page.getByText('Export coming soon')).toBeVisible();
  });

  test('should display engagement delta in compare mode', async ({ page }) => {
    await page.goto('/analytics');
    
    // Enable compare mode
    await page.getByRole('button', { name: /compare periods/i }).click();
    
    // Check for positive delta indicators
    const trendingUp = page.locator('.text-green-500');
    await expect(trendingUp.first()).toBeVisible();
    
    // Check for "vs prev" text
    await expect(page.getByText(/vs prev/i)).toBeVisible();
  });
});