# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: calendar.test.ts >> Calendar Page >> should show quick-add button on day hover
- Location: tests/calendar.test.ts:276:3

# Error details

```
TimeoutError: locator.hover: Timeout 10000ms exceeded.
Call log:
  - waiting for locator('.min-h-\\[110px\\]').first()

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
  - alert [ref=f1e23]
```

# Test source

```ts
  181 | 
  182 |   test('should show new post button', async ({ page }) => {
  183 |     await page.goto('/calendar');
  184 |     
  185 |     // Check for new post button
  186 |     const newPostButton = page.getByRole('link', { name: /New Post/i });
  187 |     await expect(newPostButton).toBeVisible();
  188 |   });
  189 | 
  190 |   test('should navigate to content creation when clicking new post', async ({ page }) => {
  191 |     await page.goto('/calendar');
  192 |     
  193 |     // Click new post button
  194 |     const newPostButton = page.getByRole('link', { name: /New Post/i });
  195 |     await newPostButton.click();
  196 |     
  197 |     // Should navigate to content creation
  198 |     await expect(page).toHaveURL('/content/new');
  199 |   });
  200 | 
  201 |   test('should allow selecting a day', async ({ page }) => {
  202 |     await page.goto('/calendar');
  203 |     
  204 |     // Click on a day cell
  205 |     const dayCell = page.locator('.min-h-\\[110px\\]').first();
  206 |     await dayCell.click();
  207 |     
  208 |     // Check for day detail panel to appear
  209 |     await expect(page.getByRole('heading', { name: /\w+, \w+ \d+/i })).toBeVisible();
  210 |   });
  211 | 
  212 |   test('should show day detail panel when day is selected', async ({ page }) => {
  213 |     await page.goto('/calendar');
  214 |     
  215 |     // Click on a day with posts
  216 |     const dayCell = page.locator('.min-h-\\[110px\\]').first();
  217 |     await dayCell.click();
  218 |     
  219 |     // Check for detail panel
  220 |     await expect(page.getByText(/Schedule for this day/i)).toBeVisible();
  221 |   });
  222 | 
  223 |   test('should show posts in day detail panel', async ({ page }) => {
  224 |     await page.goto('/calendar');
  225 |     
  226 |     // Click on a day with posts
  227 |     const dayCell = page.locator('.min-h-\\[110px\\]').first();
  228 |     await dayCell.click();
  229 |     
  230 |     // Check for post content in detail panel
  231 |     await expect(page.getByText(/scheduled post/i)).toBeVisible();
  232 |   });
  233 | 
  234 |   test('should show platform badges in day detail panel', async ({ page }) => {
  235 |     await page.goto('/calendar');
  236 |     
  237 |     // Click on a day with posts
  238 |     const dayCell = page.locator('.min-h-\\[110px\\]').first();
  239 |     await dayCell.click();
  240 |     
  241 |     // Check for platform badges
  242 |     await expect(page.locator('.badge')).toBeVisible();
  243 |   });
  244 | 
  245 |   test('should show schedule button in day detail panel', async ({ page }) => {
  246 |     await page.goto('/calendar');
  247 |     
  248 |     // Click on a day
  249 |     const dayCell = page.locator('.min-h-\\[110px\\]').first();
  250 |     await dayCell.click();
  251 |     
  252 |     // Check for schedule button
  253 |     await expect(page.getByRole('link', { name: /Schedule for this day/i })).toBeVisible();
  254 |   });
  255 | 
  256 |   test('should show empty state when day has no posts', async ({ page }) => {
  257 |     // Mock empty posts for a specific day
  258 |     await page.route('**/api/posts/scheduled', async (route) => {
  259 |       await route.fulfill({
  260 |         status: 200,
  261 |         contentType: 'application/json',
  262 |         body: JSON.stringify([]),
  263 |       });
  264 |     });
  265 |     
  266 |     await page.goto('/calendar');
  267 |     
  268 |     // Click on a day
  269 |     const dayCell = page.locator('.min-h-\\[110px\\]').first();
  270 |     await dayCell.click();
  271 |     
  272 |     // Check for empty state message
  273 |     await expect(page.getByText('Nothing scheduled')).toBeVisible();
  274 |   });
  275 | 
  276 |   test('should show quick-add button on day hover', async ({ page }) => {
  277 |     await page.goto('/calendar');
  278 |     
  279 |     // Hover over a day cell
  280 |     const dayCell = page.locator('.min-h-\\[110px\\]').first();
> 281 |     await dayCell.hover();
      |                   ^ TimeoutError: locator.hover: Timeout 10000ms exceeded.
  282 |     
  283 |     // Check for quick-add button (appears on hover)
  284 |     const quickAddButton = dayCell.locator('a').filter({ hasText: '' });
  285 |     await expect(quickAddButton).toBeVisible();
  286 |   });
  287 | 
  288 |   test('should show legend with instructions', async ({ page }) => {
  289 |     await page.goto('/calendar');
  290 |     
  291 |     // Check for legend
  292 |     await expect(page.getByText('Today')).toBeVisible();
  293 |     await expect(page.getByText(/Drag a post chip/i)).toBeVisible();
  294 |   });
  295 | 
  296 |   test('should allow dragging posts to reschedule', async ({ page }) => {
  297 |     await page.goto('/calendar');
  298 |     
  299 |     // Find a post chip
  300 |     const postChip = page.locator('.cursor-grab').first();
  301 |     await expect(postChip).toBeVisible();
  302 |     
  303 |     // Note: Full drag-and-drop testing requires more complex setup
  304 |     // This test verifies the chip is draggable
  305 |     await expect(postChip).toHaveAttribute('draggable', 'true');
  306 |   });
  307 | 
  308 |   test('should display multiple platform indicators for multi-platform posts', async ({ page }) => {
  309 |     // Mock a post with multiple platforms
  310 |     await page.route('**/api/posts/scheduled', async (route) => {
  311 |       const today = new Date();
  312 |       await route.fulfill({
  313 |         status: 200,
  314 |         contentType: 'application/json',
  315 |         body: JSON.stringify([
  316 |           {
  317 |             id: 'post-1',
  318 |             content_text: 'Multi-platform post',
  319 |             scheduled_at: today.toISOString(),
  320 |             targets: [
  321 |               {
  322 |                 social_account_id: '1',
  323 |                 social_account: { id: '1', platform: 'linkedin', username: 'testuser' },
  324 |               },
  325 |               {
  326 |                 social_account_id: '2',
  327 |                 social_account: { id: '2', platform: 'twitter', username: 'testuser' },
  328 |               },
  329 |             ],
  330 |           },
  331 |         ]),
  332 |       });
  333 |     });
  334 |     
  335 |     await page.goto('/calendar');
  336 |     
  337 |     // Check for multiple platform indicators
  338 |     const platformIndicators = page.locator('.rounded-full');
  339 |     await expect(platformIndicators).toHaveCount.gte(1);
  340 |   });
  341 | 
  342 |   test('should handle overflow when many posts in one day', async ({ page }) => {
  343 |     // Mock many posts for a single day
  344 |     const today = new Date();
  345 |     const manyPosts = Array.from({ length: 5 }, (_, i) => ({
  346 |       id: `post-${i}`,
  347 |       content_text: `Post number ${i + 1}`,
  348 |       scheduled_at: today.toISOString(),
  349 |       targets: [{
  350 |         social_account_id: '1',
  351 |         social_account: { id: '1', platform: 'linkedin', username: 'testuser' },
  352 |       }],
  353 |     }));
  354 |     
  355 |     await page.route('**/api/posts/scheduled', async (route) => {
  356 |       await route.fulfill({
  357 |         status: 200,
  358 |         contentType: 'application/json',
  359 |         body: JSON.stringify(manyPosts),
  360 |       });
  361 |     });
  362 |     
  363 |     await page.goto('/calendar');
  364 |     
  365 |     // Check for "more" indicator
  366 |     await expect(page.getByText(/\+ \d+ more/i)).toBeVisible();
  367 |   });
  368 | 
  369 |   test('should show time in day detail panel', async ({ page }) => {
  370 |     await page.goto('/calendar');
  371 |     
  372 |     // Click on a day with posts
  373 |     const dayCell = page.locator('.min-h-\\[110px\\]').first();
  374 |     await dayCell.click();
  375 |     
  376 |     // Check for time indicator
  377 |     await expect(page.locator('.text-muted-foreground').filter({ hasText: /\d+:\d+/ })).toBeVisible();
  378 |   });
  379 | });
```