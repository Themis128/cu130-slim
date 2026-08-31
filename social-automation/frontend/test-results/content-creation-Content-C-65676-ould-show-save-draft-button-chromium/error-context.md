# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: content-creation.test.ts >> Content Creation Page >> should show save draft button
- Location: tests/content-creation.test.ts:266:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('button', { name: /Save Draft/i })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByRole('button', { name: /Save Draft/i })

```

```yaml
- heading "Welcome back" [level=3]
- paragraph: Sign in to your account to continue
- text: Email
- textbox "Email":
  - /placeholder: you@example.com
- text: Password
- link "Forgot password?":
  - /url: /forgot-password
- textbox "Password":
  - /placeholder: ••••••••
- button "Sign in"
- paragraph:
  - text: Don't have an account?
  - link "Sign up":
    - /url: /register
- alert
```

# Test source

```ts
  171 | 
  172 |   test('should show platform-specific character limits', async ({ page }) => {
  173 |     await page.goto('/content/new');
  174 |     
  175 |     // Select LinkedIn first
  176 |     await page.getByRole('button', { name: /LinkedIn/i }).first().click();
  177 |     
  178 |     // Type content
  179 |     const textarea = page.getByPlaceholder('What do you want to share?');
  180 |     await textarea.fill('Test content');
  181 |     
  182 |     // Check for LinkedIn character count indicator
  183 |     await expect(page.getByText(/LinkedIn/i)).toBeVisible();
  184 |     await expect(page.getByText(/remaining/i)).toBeVisible();
  185 |   });
  186 | 
  187 |   test('should display tone selection options', async ({ page }) => {
  188 |     await page.goto('/content/new');
  189 |     
  190 |     // Check for all tone options
  191 |     await expect(page.getByRole('button', { name: /Professional/i })).toBeVisible();
  192 |     await expect(page.getByRole('button', { name: /Casual/i })).toBeVisible();
  193 |     await expect(page.getByRole('button', { name: /Witty/i })).toBeVisible();
  194 |     await expect(page.getByRole('button', { name: /Inspirational/i })).toBeVisible();
  195 |     await expect(page.getByRole('button', { name: /Educational/i })).toBeVisible();
  196 |   });
  197 | 
  198 |   test('should allow tone selection', async ({ page }) => {
  199 |     await page.goto('/content/new');
  200 |     
  201 |     // Select Witty tone
  202 |     const wittyButton = page.getByRole('button', { name: /Witty/i });
  203 |     await wittyButton.click();
  204 |     
  205 |     // Verify it's selected
  206 |     await expect(wittyButton).toHaveClass(/border-primary/);
  207 |   });
  208 | 
  209 |   test('should generate AI content', async ({ page }) => {
  210 |     await page.goto('/content/new');
  211 |     
  212 |     // Select a platform first
  213 |     await page.getByRole('button', { name: /LinkedIn/i }).first().click();
  214 |     
  215 |     // Click generate button
  216 |     const generateButton = page.getByRole('button', { name: /Generate/i });
  217 |     await generateButton.click();
  218 |     
  219 |     // Wait for AI generation to complete
  220 |     await expect(page.getByText('AI content generated')).toBeVisible();
  221 |     
  222 |     // Check that content was generated
  223 |     const textarea = page.getByPlaceholder('What do you want to share?');
  224 |     await expect(textarea).toHaveValue(/AI-generated content/i);
  225 |   });
  226 | 
  227 |   test('should show live preview when platform is selected', async ({ page }) => {
  228 |     await page.goto('/content/new');
  229 |     
  230 |     // Select LinkedIn
  231 |     await page.getByRole('button', { name: /LinkedIn/i }).first().click();
  232 |     
  233 |     // Type content
  234 |     const textarea = page.getByPlaceholder('What do you want to share?');
  235 |     await textarea.fill('Test post content');
  236 |     
  237 |     // Check for live preview section
  238 |     await expect(page.getByRole('heading', { name: 'Live Preview' })).toBeVisible();
  239 |     
  240 |     // Check for platform name in preview
  241 |     await expect(page.getByText('LinkedIn')).toBeVisible();
  242 |   });
  243 | 
  244 |   test('should allow switching between platform previews', async ({ page }) => {
  245 |     await page.goto('/content/new');
  246 |     
  247 |     // Select both LinkedIn and Twitter
  248 |     await page.getByRole('button', { name: /LinkedIn/i }).first().click();
  249 |     await page.getByRole('button', { name: /Twitter/i }).first().click();
  250 |     
  251 |     // Type content
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
> 271 |     await expect(saveDraftButton).toBeVisible();
      |                                   ^ Error: expect(locator).toBeVisible() failed
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
  352 |     await carouselButton.click();
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
```