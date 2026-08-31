# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: content-creation.test.ts >> Content Creation Page >> should load content creation page successfully
- Location: tests/content-creation.test.ts:77:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('heading', { name: 'New Post' })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByRole('heading', { name: 'New Post' })

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
  1   | import { test, expect } from '@playwright/test';
  2   | 
  3   | test.describe('Content Creation Page', () => {
  4   |   test.beforeEach(async ({ page }) => {
  5   |     // Mock authentication
  6   |     await page.goto('/dashboard');
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
> 82  |     await expect(page.getByRole('heading', { name: 'New Post' })).toBeVisible();
      |                                                                   ^ Error: expect(locator).toBeVisible() failed
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
  107 |     
  108 |     // Check for connected platforms
  109 |     await expect(page.getByText('LinkedIn')).toBeVisible();
  110 |     await expect(page.getByText('Twitter / X')).toBeVisible();
  111 |     
  112 |     // Check for unconnected platforms (disabled)
  113 |     await expect(page.getByText('Instagram')).toBeVisible();
  114 |     await expect(page.getByText('Facebook')).toBeVisible();
  115 |     await expect(page.getByText('Threads')).toBeVisible();
  116 |   });
  117 | 
  118 |   test('should allow platform selection', async ({ page }) => {
  119 |     await page.goto('/content/new');
  120 |     
  121 |     // Select LinkedIn
  122 |     const linkedinButton = page.getByRole('button', { name: /LinkedIn/i }).first();
  123 |     await linkedinButton.click();
  124 |     
  125 |     // Verify it's selected
  126 |     await expect(linkedinButton).toHaveClass(/border-primary/);
  127 |     
  128 |     // Select Twitter as well
  129 |     const twitterButton = page.getByRole('button', { name: /Twitter/i }).first();
  130 |     await twitterButton.click();
  131 |     
  132 |     // Verify both are selected
  133 |     await expect(twitterButton).toHaveClass(/border-primary/);
  134 |   });
  135 | 
  136 |   test('should show error when selecting unconnected platform', async ({ page }) => {
  137 |     await page.goto('/content/new');
  138 |     
  139 |     // Try to select Instagram (unconnected)
  140 |     const instagramButton = page.getByRole('button', { name: /Instagram/i }).first();
  141 |     await instagramButton.click();
  142 |     
  143 |     // Check for error toast
  144 |     await expect(page.getByText(/Connect your instagram account first/i)).toBeVisible();
  145 |   });
  146 | 
  147 |   test('should allow content typing in editor', async ({ page }) => {
  148 |     await page.goto('/content/new');
  149 |     
  150 |     // Find the content textarea
  151 |     const textarea = page.getByPlaceholder('What do you want to share?');
  152 |     await expect(textarea).toBeVisible();
  153 |     
  154 |     // Type content
  155 |     await textarea.fill('This is a test post for social media');
  156 |     
  157 |     // Verify content is entered
  158 |     await expect(textarea).toHaveValue('This is a test post for social media');
  159 |   });
  160 | 
  161 |   test('should display character count', async ({ page }) => {
  162 |     await page.goto('/content/new');
  163 |     
  164 |     // Type content
  165 |     const textarea = page.getByPlaceholder('What do you want to share?');
  166 |     await textarea.fill('Test content');
  167 |     
  168 |     // Check for character count
  169 |     await expect(page.getByText('12 chars')).toBeVisible();
  170 |   });
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
```