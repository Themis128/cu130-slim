# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: analytics.test.ts >> Analytics Page >> should toggle compare mode
- Location: tests/analytics.test.ts:244:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText(/total impressions/i)
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText(/total impressions/i)

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
  229 |     await page.getByRole('combobox', { name: /platform filter/i }).click();
  230 |     // Select Instagram
  231 |     await page.getByRole('option', { name: /instagram/i }).click();
  232 | 
  233 |     // Wait for platform metrics to update (should show only Instagram)
  234 |     await expect(page.getByText(/instagram/i)).toBeVisible();
  235 |     await expect(page.getByText(/facebook/i)).not.toBeVisible();
  236 |     await expect(page.getByText(/5,000/i)).toBeVisible();
  237 |     await expect(page.getByText(/7,345/i)).not.toBeVisible();
  238 | 
  239 |     // Check that top posts are filtered (if we had a platform param)
  240 |     // The top posts mock uses the platform from query, so it should show Instagram
  241 |     await expect(page.getByText(/this is a top performing post/i)).toBeVisible();
  242 |   });
  243 | 
  244 |   test('should toggle compare mode', async ({ page }) => {
  245 |     // Mock API requests
  246 |     await page.route('**/api/v1/analytics/overview', async (route) => {
  247 |       await route.fulfill({
  248 |         status: 200,
  249 |         contentType: 'application/json',
  250 |         body: JSON.stringify({
  251 |           total_impressions: 12345,
  252 |           total_engagement: 678,
  253 |           engagement_rate: 5.5,
  254 |           total_posts: 10,
  255 |         }),
  256 |       });
  257 |     });
  258 | 
  259 |     await page.route('**/api/v1/analytics/platforms', async (route) => {
  260 |       await route.fulfill({
  261 |         status: 200,
  262 |         contentType: 'application/json',
  263 |         body: JSON.stringify([
  264 |           {
  265 |             platform: 'instagram',
  266 |             total_impressions: 5000,
  267 |             total_engagement: 300,
  268 |             engagement_rate: 6.0,
  269 |             published_count: 5,
  270 |             posts_count: 5,
  271 |           },
  272 |         ]),
  273 |       });
  274 |     });
  275 | 
  276 |     await page.route('**/api/v1/analytics/top-posts', async (route) => {
  277 |       await route.fulfill({
  278 |         status: 200,
  279 |         contentType: 'application/json',
  280 |         body: JSON.stringify([
  281 |           {
  282 |             post_id: '1',
  283 |             content: 'This is a top performing post',
  284 |             platform: 'instagram',
  285 |             likes: 100,
  286 |             comments: 20,
  287 |             shares: 10,
  288 |             published_at: '2026-08-20T10:00:00Z',
  289 |             created_at: '2026-08-20T10:00:00Z',
  290 |           },
  291 |         ]),
  292 |       });
  293 |     });
  294 | 
  295 |     // For engagement trends, when compare mode is on, it requests double the days
  296 |     await page.route('**/api/v1/analytics/engagement-trends', async (route) => {
  297 |       const url = new URL(route.request().url());
  298 |       const days = parseInt(url.searchParams.get('days') || '30');
  299 |       // If days is 60 (compare mode on), return 60 days of data
  300 |       const data = [];
  301 |       for (let i = 0; i < days; i++) {
  302 |         data.push({ date: `2026-08-${(i % 30) + 1}`, value: (i % 30) + 10 });
  303 |       }
  304 |       await route.fulfill({
  305 |         status: 200,
  306 |         contentType: 'application/json',
  307 |         body: JSON.stringify(data),
  308 |       });
  309 |     });
  310 | 
  311 |     await page.goto('/dashboard/analytics');
  312 | 
  313 |     // Wait for initial load
> 314 |     await expect(page.getByText(/total impressions/i)).toBeVisible();
      |                                                        ^ Error: expect(locator).toBeVisible() failed
  315 | 
  316 |     // Toggle compare mode
  317 |     await page.getByRole('checkbox', { name: /compare mode/i }).check();
  318 | 
  319 |     // Wait for the trend chart to update (should now show comparison)
  320 |     // We can check for some text that indicates comparison, or just wait for the request
  321 |     await page.waitForTimeout(500); // Wait for re-render
  322 | 
  323 |     // The page should still show the overview metrics
  324 |     await expect(page.getByText(/total impressions/i)).toBeVisible();
  325 |   });
  326 | });
  327 | 
```