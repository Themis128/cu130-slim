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

Locator: getByText(/total engagement/i)
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText(/total engagement/i)

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
  5   |     // Mock API requests — shapes match the real backend endpoints
  6   |     await page.route('**/api/v1/analytics/overview', async (route) => {
  7   |       await route.fulfill({
  8   |         status: 200,
  9   |         contentType: 'application/json',
  10  |         body: JSON.stringify({
  11  |           total_posts: 10,
  12  |           published_posts: 6,
  13  |           scheduled_posts: 2,
  14  |           draft_posts: 1,
  15  |           failed_posts: 1,
  16  |           connected_accounts: 3,
  17  |           total_followers: 1250,
  18  |           total_engagement: 678,
  19  |         }),
  20  |       });
  21  |     });
  22  | 
  23  |     await page.route('**/api/v1/analytics/platforms', async (route) => {
  24  |       await route.fulfill({
  25  |         status: 200,
  26  |         contentType: 'application/json',
  27  |         body: JSON.stringify([
  28  |           {
  29  |             platform: 'linkedin',
  30  |             posts_count: 5,
  31  |             published_count: 4,
  32  |             scheduled_count: 1,
  33  |             total_engagement: 300,
  34  |             total_impressions: 5000,
  35  |             engagement_rate: 6.0,
  36  |           },
  37  |           {
  38  |             platform: 'facebook',
  39  |             posts_count: 5,
  40  |             published_count: 2,
  41  |             scheduled_count: 1,
  42  |             total_engagement: 378,
  43  |             total_impressions: 7345,
  44  |             engagement_rate: 5.1,
  45  |           },
  46  |         ]),
  47  |       });
  48  |     });
  49  | 
  50  |     await page.route('**/api/v1/analytics/top-posts', async (route) => {
  51  |       await route.fulfill({
  52  |         status: 200,
  53  |         contentType: 'application/json',
  54  |         body: JSON.stringify([
  55  |           {
  56  |             post_id: '1',
  57  |             content_text: 'This is a top performing post',
  58  |             platform: 'linkedin',
  59  |             impressions: 5000,
  60  |             engagement: 130,
  61  |             engagement_rate: 2.6,
  62  |             published_at: '2026-08-20T10:00:00Z',
  63  |           },
  64  |         ]),
  65  |       });
  66  |     });
  67  | 
  68  |     await page.route('**/api/v1/analytics/engagement', async (route) => {
  69  |       await route.fulfill({
  70  |         status: 200,
  71  |         contentType: 'application/json',
  72  |         body: JSON.stringify([
  73  |           { date: '2026-08-01', likes: 5, comments: 2, shares: 1, clicks: 2, total: 10 },
  74  |           { date: '2026-08-02', likes: 10, comments: 4, shares: 2, clicks: 4, total: 20 },
  75  |           { date: '2026-08-03', likes: 15, comments: 6, shares: 3, clicks: 6, total: 30 },
  76  |         ]),
  77  |       });
  78  |     });
  79  | 
  80  |     await page.route('**/api/v1/analytics/followers', async (route) => {
  81  |       await route.fulfill({
  82  |         status: 200,
  83  |         contentType: 'application/json',
  84  |         body: JSON.stringify([
  85  |           { platform: 'linkedin', followers: 1250, change: 0 },
  86  |         ]),
  87  |       });
  88  |     });
  89  | 
  90  |     await page.goto('/dashboard/analytics');
  91  | 
  92  |     // Wait for page to load — check for KPI cards
> 93  |     await expect(page.getByText(/total engagement/i)).toBeVisible();
      |                                                       ^ Error: expect(locator).toBeVisible() failed
  94  |     await expect(page.getByText(/678/i)).toBeVisible();
  95  |     await expect(page.getByText(/posts published/i)).toBeVisible();
  96  |     await expect(page.getByText(/6/i)).toBeVisible();
  97  |     await expect(page.getByText(/connected accounts/i)).toBeVisible();
  98  |     await expect(page.getByText(/total followers/i)).toBeVisible();
  99  |     await expect(page.getByText(/1,250/i)).toBeVisible();
  100 | 
  101 |     // Check platform metrics
  102 |     await expect(page.getByText(/linkedin/i)).toBeVisible();
  103 |     await expect(page.getByText(/facebook/i)).toBeVisible();
  104 | 
  105 |     // Check top posts section
  106 |     await expect(page.getByText(/this is a top performing post/i)).toBeVisible();
  107 |   });
  108 | 
  109 |   test('should show loading state when data is fetching', async ({ page }) => {
  110 |     // Mock API requests to delay
  111 |     await page.route('**/api/v1/analytics/overview', async (route) => {
  112 |       return new Promise<void>((resolve) => {
  113 |         (window as any).resolveOverview = resolve;
  114 |       });
  115 |     });
  116 | 
  117 |     await page.route('**/api/v1/analytics/platforms', async (route) => {
  118 |       return new Promise<void>((resolve) => {
  119 |         (window as any).resolvePlatforms = resolve;
  120 |       });
  121 |     });
  122 | 
  123 |     await page.route('**/api/v1/analytics/top-posts', async (route) => {
  124 |       return new Promise<void>((resolve) => {
  125 |         (window as any).resolveTopPosts = resolve;
  126 |       });
  127 |     });
  128 | 
  129 |     await page.route('**/api/v1/analytics/engagement', async (route) => {
  130 |       return new Promise<void>((resolve) => {
  131 |         (window as any).resolveTrends = resolve;
  132 |       });
  133 |     });
  134 | 
  135 |     await page.route('**/api/v1/analytics/followers', async (route) => {
  136 |       await route.fulfill({
  137 |         status: 200,
  138 |         contentType: 'application/json',
  139 |         body: JSON.stringify([]),
  140 |       });
  141 |     });
  142 | 
  143 |     await page.goto('/dashboard/analytics');
  144 | 
  145 |     // Check for loading skeletons or spinner
  146 |     await expect(page.getByText(/loading/i)).toBeVisible({ timeout: 5000 });
  147 | 
  148 |     // Resolve the requests
  149 |     (window as any).resolveOverview();
  150 |     (window as any).resolvePlatforms();
  151 |     (window as any).resolveTopPosts();
  152 |     (window as any).resolveTrends();
  153 |     await page.waitForTimeout(100);
  154 | 
  155 |     // After resolution, we should see the data
  156 |     await expect(page.getByText(/total engagement/i)).toBeVisible();
  157 |   });
  158 | 
  159 |   test('should display engagement chart data', async ({ page }) => {
  160 |     await page.route('**/api/v1/analytics/overview', async (route) => {
  161 |       await route.fulfill({
  162 |         status: 200,
  163 |         contentType: 'application/json',
  164 |         body: JSON.stringify({
  165 |           total_posts: 10,
  166 |           published_posts: 6,
  167 |           scheduled_posts: 2,
  168 |           draft_posts: 1,
  169 |           failed_posts: 1,
  170 |           connected_accounts: 3,
  171 |           total_followers: 1250,
  172 |           total_engagement: 678,
  173 |         }),
  174 |       });
  175 |     });
  176 | 
  177 |     await page.route('**/api/v1/analytics/platforms', async (route) => {
  178 |       await route.fulfill({
  179 |         status: 200,
  180 |         contentType: 'application/json',
  181 |         body: JSON.stringify([
  182 |           {
  183 |             platform: 'linkedin',
  184 |             posts_count: 5,
  185 |             published_count: 4,
  186 |             scheduled_count: 1,
  187 |             total_engagement: 300,
  188 |             total_impressions: 5000,
  189 |             engagement_rate: 6.0,
  190 |           },
  191 |         ]),
  192 |       });
  193 |     });
```