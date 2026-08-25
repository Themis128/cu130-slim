# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: calendar.test.ts >> Calendar Page >> should handle overflow when many posts in one day
- Location: tests/calendar.test.ts:342:3

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: page.goto: Test timeout of 30000ms exceeded.
Call log:
  - navigating to "http://localhost:3001/calendar", waiting until "load"

```

# Page snapshot

```yaml
- generic [active]:
  - button "Open Next.js Dev Tools" [ref=f1e6] [cursor=pointer]:
    - generic [ref=f1e9]:
      - text: Compiling
      - generic [ref=f1e10]:
        - generic [ref=f1e11]: .
        - generic [ref=f1e12]: .
        - generic [ref=f1e13]: .
  - alert [ref=f1e14]
```

# Test source

```ts
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
  281 |     await dayCell.hover();
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
> 363 |     await page.goto('/calendar');
      |                ^ Error: page.goto: Test timeout of 30000ms exceeded.
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