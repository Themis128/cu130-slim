# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: content-creation.test.ts >> Content Creation Page >> should navigate to different content types
- Location: tests/content-creation.test.ts:347:3

# Error details

```
TimeoutError: locator.click: Timeout 10000ms exceeded.
Call log:
  - waiting for getByRole('button', { name: 'Carousel' })

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
  252 |     const textarea = page.getByPlaceholder('What do you want to share?');
  253 |     await textarea.fill('Test post content');
  254 |     
  255 |     // Check for platform switcher buttons in preview
  256 |     const platformSwitchers = page.locator('.lg\\:sticky button').filter({ hasText: /^[in𝕏@]$/ });
  257 |     await expect(platformSwitchers).toHaveCount(2);
  258 |     
  259 |     // Click Twitter switcher
  260 |     await platformSwitchers.nth(1).click();
  261 |     
  262 |     // Verify preview updates (Twitter preview should be different)
  263 |     await expect(page.getByText('Twitter / X')).toBeVisible();
  264 |   });
  265 | 
  266 |   test('should show save draft button', async ({ page }) => {
  267 |     await page.goto('/content/new');
  268 |     
  269 |     // Check for save draft button
  270 |     const saveDraftButton = page.getByRole('button', { name: /Save Draft/i });
  271 |     await expect(saveDraftButton).toBeVisible();
  272 |   });
  273 | 
  274 |   test('should show schedule button', async ({ page }) => {
  275 |     await page.goto('/content/new');
  276 |     
  277 |     // Check for schedule button
  278 |     const scheduleButton = page.getByRole('button', { name: /Schedule/i });
  279 |     await expect(scheduleButton).toBeVisible();
  280 |   });
  281 | 
  282 |   test('should show publish now button', async ({ page }) => {
  283 |     await page.goto('/content/new');
  284 |     
  285 |     // Check for publish now button
  286 |     const publishButton = page.getByRole('button', { name: /Publish Now/i });
  287 |     await expect(publishButton).toBeVisible();
  288 |   });
  289 | 
  290 |   test('should open schedule dialog', async ({ page }) => {
  291 |     await page.goto('/content/new');
  292 |     
  293 |     // Click schedule button
  294 |     const scheduleButton = page.getByRole('button', { name: /Schedule/i });
  295 |     await scheduleButton.click();
  296 |     
  297 |     // Check for dialog
  298 |     await expect(page.getByRole('dialog')).toBeVisible();
  299 |     await expect(page.getByRole('heading', { name: 'Schedule Post' })).toBeVisible();
  300 |     
  301 |     // Check for datetime input
  302 |     await expect(page.getByRole('textbox')).toBeVisible();
  303 |   });
  304 | 
  305 |   test('should validate content before publishing', async ({ page }) => {
  306 |     await page.goto('/content/new');
  307 |     
  308 |     // Try to publish without content
  309 |     const publishButton = page.getByRole('button', { name: /Publish Now/i });
  310 |     await publishButton.click();
  311 |     
  312 |     // Check for validation error
  313 |     await expect(page.getByText('Please add some content')).toBeVisible();
  314 |   });
  315 | 
  316 |   test('should validate platform selection before publishing', async ({ page }) => {
  317 |     await page.goto('/content/new');
  318 |     
  319 |     // Add content but don't select platform
  320 |     const textarea = page.getByPlaceholder('What do you want to share?');
  321 |     await textarea.fill('Test content');
  322 |     
  323 |     // Try to publish
  324 |     const publishButton = page.getByRole('button', { name: /Publish Now/i });
  325 |     await publishButton.click();
  326 |     
  327 |     // Check for validation error
  328 |     await expect(page.getByText('Select at least one platform')).toBeVisible();
  329 |   });
  330 | 
  331 |   test('should successfully publish post', async ({ page }) => {
  332 |     await page.goto('/content/new');
  333 |     
  334 |     // Select platform and add content
  335 |     await page.getByRole('button', { name: /LinkedIn/i }).first().click();
  336 |     const textarea = page.getByPlaceholder('What do you want to share?');
  337 |     await textarea.fill('Test content to publish');
  338 |     
  339 |     // Publish
  340 |     const publishButton = page.getByRole('button', { name: /Publish Now/i });
  341 |     await publishButton.click();
  342 |     
  343 |     // Check for success message
  344 |     await expect(page.getByText('Post published')).toBeVisible();
  345 |   });
  346 | 
  347 |   test('should navigate to different content types', async ({ page }) => {
  348 |     await page.goto('/content/new');
  349 |     
  350 |     // Click on Carousel
  351 |     const carouselButton = page.getByRole('button', { name: 'Carousel' });
> 352 |     await carouselButton.click();
      |                          ^ TimeoutError: locator.click: Timeout 10000ms exceeded.
  353 |     
  354 |     // Should navigate to carousel creation page
  355 |     await expect(page).toHaveURL('/content/carousel/new');
  356 |   });
  357 | 
  358 |   test('should show link to accounts page when no platforms selected', async ({ page }) => {
  359 |     await page.goto('/content/new');
  360 |     
  361 |     // Check for help text when no platforms selected
  362 |     await expect(page.getByText('Need to connect accounts?')).toBeVisible();
  363 |     await expect(page.getByRole('link', { name: 'Go to Accounts' })).toBeVisible();
  364 |   });
  365 | 
  366 |   test('should display media upload button', async ({ page }) => {
  367 |     await page.goto('/content/new');
  368 |     
  369 |     // Check for media upload button
  370 |     const mediaButton = page.getByRole('button', { name: /Media/i });
  371 |     await expect(mediaButton).toBeVisible();
  372 |   });
  373 | 
  374 |   test('should show content repurpose button when content and platforms selected', async ({ page }) => {
  375 |     await page.goto('/content/new');
  376 |     
  377 |     // Select platform and add content
  378 |     await page.getByRole('button', { name: /LinkedIn/i }).first().click();
  379 |     const textarea = page.getByPlaceholder('What do you want to share?');
  380 |     await textarea.fill('Test content for repurposing');
  381 |     
  382 |     // Check for repurpose button
  383 |     const repurposeButton = page.getByRole('button', { name: /Repurpose/i });
  384 |     await expect(repurposeButton).toBeVisible();
  385 |   });
  386 | 
  387 |   test('should show preview placeholder when no platform selected', async ({ page }) => {
  388 |     await page.goto('/content/new');
  389 |     
  390 |     // Check for preview placeholder
  391 |     await expect(page.getByText('Select a platform to see a live preview')).toBeVisible();
  392 |     await expect(page.getByText('Your post will appear here as you type')).toBeVisible();
  393 |   });
  394 | });
```