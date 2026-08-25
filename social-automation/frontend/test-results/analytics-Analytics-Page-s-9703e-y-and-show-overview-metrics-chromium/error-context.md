# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: analytics.test.ts >> Analytics Page >> should load successfully and show overview metrics
- Location: tests/analytics.test.ts:4:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('heading', { name: /overview/i })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByRole('heading', { name: /overview/i })

```

```yaml
- heading "404" [level=1]
- heading "This page could not be found." [level=2]
- alert
```

# Test source

```ts
  1   | import { test, expect } from '@playwright/test';
  2   | 
  3   | test.describe('Analytics Page', () => {
  4   |   test('should load successfully and show overview metrics', async ({ page }) => {
  5   |     // Mock API requests
  6   |     await page.route('**/api/v1/analytics/overview', async (route) => {
  7   |       await route.fulfill({
  8   |         status: 200,
  9   |         contentType: 'application/json',
  10  |         body: JSON.stringify({
  11  |           total_impressions: 12345,
  12  |           total_engagement: 678,
  13  |           engagement_rate: 5.5,
  14  |           total_posts: 10,
  15  |         }),
  16  |       });
  17  |     });
  18  | 
  19  |     await page.route('**/api/v1/analytics/platforms', async (route) => {
  20  |       await route.fulfill({
  21  |         status: 200,
  22  |         contentType: 'application/json',
  23  |         body: JSON.stringify([
  24  |           {
  25  |             platform: 'instagram',
  26  |             total_impressions: 5000,
  27  |             total_engagement: 300,
  28  |             engagement_rate: 6.0,
  29  |             published_count: 5,
  30  |             posts_count: 5,
  31  |           },
  32  |           {
  33  |             platform: 'facebook',
  34  |             total_impressions: 7345,
  35  |             total_engagement: 378,
  36  |             engagement_rate: 5.1,
  37  |             published_count: 5,
  38  |             posts_count: 5,
  39  |           },
  40  |         ]),
  41  |       });
  42  |     });
  43  | 
  44  |     await page.route('**/api/v1/analytics/top-posts', async (route) => {
  45  |       await route.fulfill({
  46  |         status: 200,
  47  |         contentType: 'application/json',
  48  |         body: JSON.stringify([
  49  |           {
  50  |             post_id: '1',
  51  |             content: 'This is a top performing post',
  52  |             platform: 'instagram',
  53  |             likes: 100,
  54  |             comments: 20,
  55  |             shares: 10,
  56  |             published_at: '2026-08-20T10:00:00Z',
  57  |             created_at: '2026-08-20T10:00:00Z',
  58  |           },
  59  |         ]),
  60  |       });
  61  |     });
  62  | 
  63  |     await page.route('**/api/v1/analytics/engagement-trends', async (route) => {
  64  |       await route.fulfill({
  65  |         status: 200,
  66  |         contentType: 'application/json',
  67  |         body: JSON.stringify([
  68  |           { date: '2026-08-01', value: 10 },
  69  |           { date: '2026-08-02', value: 20 },
  70  |           { date: '2026-08-03', value: 30 },
  71  |         ]),
  72  |       });
  73  |     });
  74  | 
  75  |     await page.goto('/dashboard/analytics');
  76  | 
  77  |     // Wait for page to load
> 78  |     await expect(page.getByRole('heading', { name: /overview/i })).toBeVisible();
      |                                                                    ^ Error: expect(locator).toBeVisible() failed
  79  |     await expect(page.getByText(/total impressions/i)).toBeVisible();
  80  |     await expect(page.getByText(/12,345/i)).toBeVisible();
  81  |     await expect(page.getByText(/total engagement/i)).toBeVisible();
  82  |     await expect(page.getByText(/678/i)).toBeVisible();
  83  |     await expect(page.getByText(/engagement rate/i)).toBeVisible();
  84  |     await expect(page.getByText(/5.5%/i)).toBeVisible();
  85  |     await expect(page.getByText(/total posts/i)).toBeVisible();
  86  |     await expect(page.getByText(/10/i)).toBeVisible();
  87  | 
  88  |     // Check platform metrics table
  89  |     await expect(page.getByText(/instagram/i)).toBeVisible();
  90  |     await expect(page.getByText(/5,000/i)).toBeVisible();
  91  |     await expect(page.getByText(/facebook/i)).toBeVisible();
  92  |     await expect(page.getByText(/7,345/i)).toBeVisible();
  93  | 
  94  |     // Check top posts section
  95  |     await expect(page.getByRole('heading', { name: /top performing posts/i })).toBeVisible();
  96  |     await expect(page.getByText(/this is a top performing post/i)).toBeVisible();
  97  |   });
  98  | 
  99  |   test('should show loading state when data is fetching', async ({ page }) => {
  100 |     // Mock API requests to delay
  101 |     await page.route('**/api/v1/analytics/overview', async (route) => {
  102 |       return new Promise<void>((resolve) => {
  103 |         (window as any).resolveOverview = resolve;
  104 |       });
  105 |     });
  106 | 
  107 |     await page.route('**/api/v1/analytics/platforms', async (route) => {
  108 |       return new Promise<void>((resolve) => {
  109 |         (window as any).resolvePlatforms = resolve;
  110 |       });
  111 |     });
  112 | 
  113 |     await page.route('**/api/v1/analytics/top-posts', async (route) => {
  114 |       return new Promise<void>((resolve) => {
  115 |         (window as any).resolveTopPosts = resolve;
  116 |       });
  117 |     });
  118 | 
  119 |     await page.route('**/api/v1/analytics/engagement-trends', async (route) => {
  120 |       return new Promise<void>((resolve) => {
  121 |         (window as any).resolveTrends = resolve;
  122 |       });
  123 |     });
  124 | 
  125 |     await page.goto('/dashboard/analytics');
  126 | 
  127 |     // Check for loading skeletons or text
  128 |     await expect(page.getByText(/loading/i)).toBeVisible();
  129 |     // Or check for skeleton elements
  130 |     await expect(page.getByRole('img', { name: /skeleton/i })).toBeVisible({ timeout: 5000 });
  131 | 
  132 |     // Resolve the requests
  133 |     (window as any).resolveOverview();
  134 |     (window as any).resolvePlatforms();
  135 |     (window as any).resolveTopPosts();
  136 |     (window as any).resolveTrends();
  137 |     await page.waitForTimeout(100);
  138 | 
  139 |     // After resolution, we should see the data
  140 |     await expect(page.getByText(/total impressions/i)).toBeVisible();
  141 |   });
  142 | 
  143 |   test('should filter by platform', async ({ page }) => {
  144 |     // Mock API requests
  145 |     await page.route('**/api/v1/analytics/overview', async (route) => {
  146 |       await route.fulfill({
  147 |         status: 200,
  148 |         contentType: 'application/json',
  149 |         body: JSON.stringify({
  150 |           total_impressions: 12345,
  151 |           total_engagement: 678,
  152 |           engagement_rate: 5.5,
  153 |           total_posts: 10,
  154 |         }),
  155 |       });
  156 |     });
  157 | 
  158 |     await page.route('**/api/v1/analytics/platforms', async (route) => {
  159 |       // This endpoint might receive a platform filter query param
  160 |       const url = new URL(route.request().url());
  161 |       const platform = url.searchParams.get('platform') || '';
  162 |       let data = [
  163 |         {
  164 |           platform: 'instagram',
  165 |           total_impressions: 5000,
  166 |           total_engagement: 300,
  167 |           engagement_rate: 6.0,
  168 |           published_count: 5,
  169 |           posts_count: 5,
  170 |         },
  171 |         {
  172 |           platform: 'facebook',
  173 |           total_impressions: 7345,
  174 |           total_engagement: 378,
  175 |           engagement_rate: 5.1,
  176 |           published_count: 5,
  177 |           posts_count: 5,
  178 |         },
```