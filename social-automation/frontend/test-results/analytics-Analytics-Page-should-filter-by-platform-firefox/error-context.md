# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: analytics.test.ts >> Analytics Page >> should filter by platform
- Location: tests/analytics.test.ts:143:3

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Tearing down "context" exceeded the test timeout of 30000ms.
```

```
Error: page.goto: Test timeout of 30000ms exceeded.
Call log:
  - navigating to "http://localhost:3001/dashboard/analytics", waiting until "load"

```

# Test source

```ts
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
> 223 |     await page.goto('/dashboard/analytics');
      |                ^ Error: page.goto: Test timeout of 30000ms exceeded.
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
  314 |     await expect(page.getByText(/total impressions/i)).toBeVisible();
  315 | 
  316 |     // Toggle compare mode
  317 |     await page.getByRole('checkbox', { name: /compare mode/i }).check();
  318 | 
  319 |     // Wait for the trend chart to update (should now show comparison)
  320 |     // We can check for some text that indicates comparison, or just wait for the request
  321 |     await page.waitForTimeout(500); // Wait for re-render
  322 | 
  323 |     // The page should still show the overview metrics
```