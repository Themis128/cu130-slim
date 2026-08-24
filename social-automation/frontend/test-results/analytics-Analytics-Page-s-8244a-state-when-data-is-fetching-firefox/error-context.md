# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: analytics.test.ts >> Analytics Page >> should show loading state when data is fetching
- Location: tests/analytics.test.ts:99:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText(/loading/i)
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText(/loading/i)

```

```yaml
- heading "404" [level=1]
- heading "This page could not be found." [level=2]
- button "Open Tanstack query devtools":
  - img
- alert
```

# Test source

```ts
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
  78  |     await expect(page.getByRole('heading', { name: /overview/i })).toBeVisible();
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
> 128 |     await expect(page.getByText(/loading/i)).toBeVisible();
      |                                              ^ Error: expect(locator).toBeVisible() failed
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
  179 |       ];
  180 |       if (platform) {
  181 |         data = data.filter((p) => p.platform === platform);
  182 |       }
  183 |       await route.fulfill({
  184 |         status: 200,
  185 |         contentType: 'application/json',
  186 |         body: JSON.stringify(data),
  187 |       });
  188 |     });
  189 | 
  190 |     await page.route('**/api/v1/analytics/top-posts', async (route) => {
  191 |       const url = new URL(route.request().url());
  192 |       const platform = url.searchParams.get('platform') || undefined;
  193 |       await route.fulfill({
  194 |         status: 200,
  195 |         contentType: 'application/json',
  196 |         body: JSON.stringify([
  197 |           {
  198 |             post_id: '1',
  199 |             content: 'This is a top performing post',
  200 |             platform: platform || 'instagram',
  201 |             likes: 100,
  202 |             comments: 20,
  203 |             shares: 10,
  204 |             published_at: '2026-08-20T10:00:00Z',
  205 |             created_at: '2026-08-20T10:00:00Z',
  206 |           },
  207 |         ]),
  208 |       });
  209 |     });
  210 | 
  211 |     await page.route('**/api/v1/analytics/engagement-trends', async (route) => {
  212 |       await route.fulfill({
  213 |         status: 200,
  214 |         contentType: 'application/json',
  215 |         body: JSON.stringify([
  216 |           { date: '2026-08-01', value: 10 },
  217 |           { date: '2026-08-02', value: 20 },
  218 |           { date: '2026-08-03', value: 30 },
  219 |         ]),
  220 |       });
  221 |     });
  222 | 
  223 |     await page.goto('/dashboard/analytics');
  224 | 
  225 |     // Wait for initial load
  226 |     await expect(page.getByText(/total impressions/i)).toBeVisible();
  227 | 
  228 |     // Click on platform filter dropdown
```