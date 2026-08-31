# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: analytics.test.ts >> Analytics Page >> should display engagement chart data
- Location: tests/analytics.test.ts:159:3

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
  194 | 
  195 |     await page.route('**/api/v1/analytics/top-posts', async (route) => {
  196 |       await route.fulfill({
  197 |         status: 200,
  198 |         contentType: 'application/json',
  199 |         body: JSON.stringify([
  200 |           {
  201 |             post_id: '1',
  202 |             content_text: 'This is a top performing post',
  203 |             platform: 'linkedin',
  204 |             impressions: 5000,
  205 |             engagement: 130,
  206 |             engagement_rate: 2.6,
  207 |             published_at: '2026-08-20T10:00:00Z',
  208 |           },
  209 |         ]),
  210 |       });
  211 |     });
  212 | 
  213 |     await page.route('**/api/v1/analytics/engagement', async (route) => {
  214 |       const url = new URL(route.request().url());
  215 |       const days = parseInt(url.searchParams.get('days') || '30');
  216 |       const data = [];
  217 |       for (let i = 0; i < Math.min(days, 30); i++) {
  218 |         data.push({
  219 |           date: `2026-08-${(i % 30) + 1}`.padStart(10, '0'),
  220 |           likes: (i % 30) + 5,
  221 |           comments: (i % 10) + 1,
  222 |           shares: (i % 5) + 1,
  223 |           clicks: (i % 8) + 2,
  224 |           total: (i % 30) + 10,
  225 |         });
  226 |       }
  227 |       await route.fulfill({
  228 |         status: 200,
  229 |         contentType: 'application/json',
  230 |         body: JSON.stringify(data),
  231 |       });
  232 |     });
  233 | 
  234 |     await page.route('**/api/v1/analytics/followers', async (route) => {
  235 |       await route.fulfill({
  236 |         status: 200,
  237 |         contentType: 'application/json',
  238 |         body: JSON.stringify([
  239 |           { platform: 'linkedin', followers: 1250, change: 0 },
  240 |         ]),
  241 |       });
  242 |     });
  243 | 
  244 |     await page.goto('/dashboard/analytics');
  245 | 
  246 |     // Wait for initial load
> 247 |     await expect(page.getByText(/total engagement/i)).toBeVisible();
      |                                                       ^ Error: expect(locator).toBeVisible() failed
  248 | 
  249 |     // Check engagement chart is rendered
  250 |     await expect(page.getByText(/engagement over time/i)).toBeVisible();
  251 | 
  252 |     // Check top posts are visible
  253 |     await expect(page.getByText(/this is a top performing post/i)).toBeVisible();
  254 |   });
  255 | });
  256 | 
```