# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: content-creation.test.ts >> Content Creation Page >> should successfully publish post
- Location: tests/content-creation.test.ts:331:3

# Error details

```
Test timeout of 30000ms exceeded while running "beforeEach" hook.
```

```
Error: page.goto: Test timeout of 30000ms exceeded.
Call log:
  - navigating to "http://localhost:3001/dashboard", waiting until "load"

```

# Test source

```ts
  1   | import { test, expect } from '@playwright/test';
  2   | 
  3   | test.describe('Content Creation Page', () => {
  4   |   test.beforeEach(async ({ page }) => {
  5   |     // Mock authentication
> 6   |     await page.goto('/dashboard');
      |                ^ Error: page.goto: Test timeout of 30000ms exceeded.
  7   |     
  8   |     // Mock connected accounts
  9   |     await page.route('**/api/accounts', async (route) => {
  10  |       await route.fulfill({
  11  |         status: 200,
  12  |         contentType: 'application/json',
  13  |         body: JSON.stringify([
  14  |           {
  15  |             id: '1',
  16  |             platform: 'linkedin',
  17  |             username: 'testuser',
  18  |             connected_at: new Date().toISOString(),
  19  |           },
  20  |           {
  21  |             id: '2',
  22  |             platform: 'twitter',
  23  |             username: 'testuser',
  24  |             connected_at: new Date().toISOString(),
  25  |           },
  26  |         ]),
  27  |       });
  28  |     });
  29  | 
  30  |     // Mock create post API
  31  |     await page.route('**/api/posts', async (route) => {
  32  |       await route.fulfill({
  33  |         status: 200,
  34  |         contentType: 'application/json',
  35  |         body: JSON.stringify({
  36  |           data: { id: 'post-123' },
  37  |         }),
  38  |       });
  39  |     });
  40  | 
  41  |     // Mock publish API
  42  |     await page.route('**/api/posts/*/publish', async (route) => {
  43  |       await route.fulfill({
  44  |         status: 200,
  45  |         contentType: 'application/json',
  46  |         body: JSON.stringify({}),
  47  |       });
  48  |     });
  49  | 
  50  |     // Mock media upload API
  51  |     await page.route('**/api/media', async (route) => {
  52  |       await route.fulfill({
  53  |         status: 200,
  54  |         contentType: 'application/json',
  55  |         body: JSON.stringify({
  56  |           id: 'media-123',
  57  |           url: 'https://example.com/image.jpg',
  58  |         }),
  59  |       });
  60  |     });
  61  | 
  62  |     // Mock AI content generation
  63  |     await page.route('**/api/ai/generate', async (route) => {
  64  |       await route.fulfill({
  65  |         status: 200,
  66  |         contentType: 'application/json',
  67  |         body: JSON.stringify({
  68  |           data: {
  69  |             content: 'This is AI-generated content about our latest product launch #innovation #tech',
  70  |             hashtags: ['innovation', 'tech'],
  71  |           },
  72  |         }),
  73  |       });
  74  |     });
  75  |   });
  76  | 
  77  |   test('should load content creation page successfully', async ({ page }) => {
  78  |     await page.goto('/content/new');
  79  |     await expect(page).toHaveURL('/content/new');
  80  |     
  81  |     // Check for main heading
  82  |     await expect(page.getByRole('heading', { name: 'New Post' })).toBeVisible();
  83  |     await expect(page.getByText('Create and schedule content across platforms')).toBeVisible();
  84  |   });
  85  | 
  86  |   test('should display content type options', async ({ page }) => {
  87  |     await page.goto('/content/new');
  88  |     
  89  |     // Check for all content type buttons
  90  |     await expect(page.getByRole('button', { name: 'Post' })).toBeVisible();
  91  |     await expect(page.getByRole('button', { name: 'Carousel' })).toBeVisible();
  92  |     await expect(page.getByRole('button', { name: 'Thread' })).toBeVisible();
  93  |     await expect(page.getByRole('button', { name: 'Poll' })).toBeVisible();
  94  |     await expect(page.getByRole('button', { name: 'Story' })).toBeVisible();
  95  |     await expect(page.getByRole('button', { name: 'Article' })).toBeVisible();
  96  |     
  97  |     // Check that Post is selected by default
  98  |     const postButton = page.getByRole('button', { name: 'Post' });
  99  |     await expect(postButton).toHaveClass(/border-primary/);
  100 |   });
  101 | 
  102 |   test('should display platform selector with connected accounts', async ({ page }) => {
  103 |     await page.goto('/content/new');
  104 |     
  105 |     // Check for platform selector heading
  106 |     await expect(page.getByRole('heading', { name: 'Platforms' })).toBeVisible();
```