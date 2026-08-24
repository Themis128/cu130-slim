# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: analytics.test.ts >> Analytics Page >> should display engagement delta in compare mode
- Location: tests/analytics.test.ts:295:3

# Error details

```
TimeoutError: locator.click: Timeout 10000ms exceeded.
Call log:
  - waiting for getByRole('button', { name: /compare periods/i })

```

# Page snapshot

```yaml
- generic [ref=f1e1]:
  - generic [ref=f1e4]:
    - generic [ref=f1e5]:
      - heading "Welcome back" [level=3] [ref=f1e6]
      - paragraph [ref=f1e7]: Sign in to your account to continue
    - generic [ref=f1e9]:
      - generic [ref=f1e10]:
        - text: Email
        - textbox "Email" [active] [ref=f1e12]:
          - /placeholder: you@example.com
      - generic [ref=f1e13]:
        - generic [ref=f1e14]:
          - generic [ref=f1e15]: Password
          - link "Forgot password?" [ref=f1e16] [cursor=pointer]:
            - /url: /forgot-password
        - textbox "Password" [ref=f1e18]:
          - /placeholder: ••••••••
      - button "Sign in" [ref=f1e19] [cursor=pointer]
    - paragraph [ref=f1e21]:
      - text: Don't have an account?
      - link "Sign up" [ref=f1e22] [cursor=pointer]:
        - /url: /register
  - button "Open Tanstack query devtools" [ref=f1e73] [cursor=pointer]
  - alert [ref=f1e122]
```

# Test source

```ts
  199 |     await page.goto('/analytics');
  200 |     
  201 |     // Check for the impressions card
  202 |     await expect(page.getByRole('heading', { name: 'Impressions by Platform' })).toBeVisible();
  203 |     await expect(page.getByText('Total impressions per platform for the selected period')).toBeVisible();
  204 |   });
  205 | 
  206 |   test('should display platform breakdown table', async ({ page }) => {
  207 |     await page.goto('/analytics');
  208 |     
  209 |     // Check for the platform breakdown card
  210 |     await expect(page.getByRole('heading', { name: 'Platform Breakdown' })).toBeVisible();
  211 |     await expect(page.getByText('Detailed metrics per platform')).toBeVisible();
  212 |     
  213 |     // Check for table headers
  214 |     await expect(page.getByText('Platform')).toBeVisible();
  215 |     await expect(page.getByText('Impressions')).toBeVisible();
  216 |     await expect(page.getByText('Engagement')).toBeVisible();
  217 |     await expect(page.getByText('Eng. Rate')).toBeVisible();
  218 |     await expect(page.getByText('Published')).toBeVisible();
  219 |     await expect(page.getByText('Total Posts')).toBeVisible();
  220 |     
  221 |     // Check for data rows
  222 |     await expect(page.getByText('twitter')).toBeVisible();
  223 |     await expect(page.getByText('linkedin')).toBeVisible();
  224 |     await expect(page.getByText('instagram')).toBeVisible();
  225 |   });
  226 | 
  227 |   test('should display top performing posts', async ({ page }) => {
  228 |     await page.goto('/analytics');
  229 |     
  230 |     // Check for the top posts card
  231 |     await expect(page.getByRole('heading', { name: 'Top Performing Posts' })).toBeVisible();
  232 |     await expect(page.getByText('Your best content by engagement')).toBeVisible();
  233 |     
  234 |     // Check for post entries
  235 |     await expect(page.getByText('#1')).toBeVisible();
  236 |     await expect(page.getByText('#2')).toBeVisible();
  237 |     
  238 |     // Check for post content snippets
  239 |     await expect(page.getByText(/top performing post/i)).toBeVisible();
  240 |   });
  241 | 
  242 |   test('should show empty state when no top posts data', async ({ page }) => {
  243 |     // Mock empty top posts response
  244 |     await page.route('**/api/top-posts', async (route) => {
  245 |       await route.fulfill({
  246 |         status: 200,
  247 |         contentType: 'application/json',
  248 |         body: JSON.stringify([]),
  249 |       });
  250 |     });
  251 |     
  252 |     await page.goto('/analytics');
  253 |     
  254 |     // Check for empty state
  255 |     await expect(page.getByText('No data for this period')).toBeVisible();
  256 |     await expect(page.getByText('Publish posts to start seeing engagement metrics')).toBeVisible();
  257 |     await expect(page.getByRole('link', { name: 'Create a post' })).toBeVisible();
  258 |   });
  259 | 
  260 |   test('should show loading state while fetching data', async ({ page }) => {
  261 |     // Delay the API response to test loading state
  262 |     await page.route('**/api/overview-metrics', async (route) => {
  263 |       await new Promise(resolve => setTimeout(resolve, 100));
  264 |       await route.fulfill({
  265 |         status: 200,
  266 |         contentType: 'application/json',
  267 |         body: JSON.stringify({
  268 |           connected_accounts: 5,
  269 |           total_engagement: 12500,
  270 |           total_followers: 45000,
  271 |           published_posts: 120,
  272 |         }),
  273 |       });
  274 |     });
  275 |     
  276 |     await page.goto('/analytics');
  277 |     
  278 |     // Check for skeleton loaders (they should be present briefly)
  279 |     const skeletons = page.locator('.animate-pulse');
  280 |     await expect(skeletons.first()).toBeVisible();
  281 |   });
  282 | 
  283 |   test('should handle export button click', async ({ page }) => {
  284 |     await page.goto('/analytics');
  285 |     
  286 |     // Find and click the export button
  287 |     const exportButton = page.getByRole('button', { name: /export/i });
  288 |     await expect(exportButton).toBeVisible();
  289 |     await exportButton.click();
  290 |     
  291 |     // Verify toast notification appears
  292 |     await expect(page.getByText('Export coming soon')).toBeVisible();
  293 |   });
  294 | 
  295 |   test('should display engagement delta in compare mode', async ({ page }) => {
  296 |     await page.goto('/analytics');
  297 |     
  298 |     // Enable compare mode
> 299 |     await page.getByRole('button', { name: /compare periods/i }).click();
      |                                                                  ^ TimeoutError: locator.click: Timeout 10000ms exceeded.
  300 |     
  301 |     // Check for positive delta indicators
  302 |     const trendingUp = page.locator('.text-green-500');
  303 |     await expect(trendingUp.first()).toBeVisible();
  304 |     
  305 |     // Check for "vs prev" text
  306 |     await expect(page.getByText(/vs prev/i)).toBeVisible();
  307 |   });
  308 | });
```