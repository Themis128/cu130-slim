# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: dashboard.test.ts >> Dashboard Page >> should load successfully and show dashboard content
- Location: tests/dashboard.test.ts:4:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('heading', { name: /dashboard/i })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByRole('heading', { name: /dashboard/i })

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
- button "Open Tanstack query devtools":
  - img
- alert
```

# Test source

```ts
  1  | import { test, expect } from '@playwright/test';
  2  | 
  3  | test.describe('Dashboard Page', () => {
  4  |   test('should load successfully and show dashboard content', async ({ page }) => {
  5  |     // Mock the API calls for the dashboard
  6  |     await page.route('**/api/overview-metrics', async (route) => {
  7  |       await route.fulfill({
  8  |         status: 200,
  9  |         contentType: 'application/json',
  10 |         body: JSON.stringify({
  11 |           total_posts: 10,
  12 |           published_posts: 5,
  13 |           scheduled_posts: 3,
  14 |           connected_accounts: 2,
  15 |         }),
  16 |       });
  17 |     });
  18 | 
  19 |     await page.route('**/api/top-posts', async (route) => {
  20 |       await route.fulfill({
  21 |         status: 200,
  22 |         contentType: 'application/json',
  23 |         body: JSON.stringify([
  24 |           {
  25 |             post_id: '1',
  26 |             content: 'This is a test post',
  27 |             platform: 'twitter',
  28 |             likes: 10,
  29 |             comments: 5,
  30 |             shares: 2,
  31 |             published_at: new Date().toISOString(),
  32 |             created_at: new Date().toISOString(),
  33 |           },
  34 |         ]),
  35 |       });
  36 |     });
  37 | 
  38 |     await page.route('**/api/scheduled-posts', async (route) => {
  39 |       await route.fulfill({
  40 |         status: 200,
  41 |         contentType: 'application/json',
  42 |         body: JSON.stringify([
  43 |           {
  44 |             post_id: '2',
  45 |             content: 'Scheduled post',
  46 |             platform: 'facebook',
  47 |             scheduled_for: new Date(Date.now() + 3600000).toISOString(),
  48 |             created_at: new Date().toISOString(),
  49 |           },
  50 |         ]),
  51 |       });
  52 |     });
  53 | 
  54 |     // Mock the advisor endpoint (if any)
  55 |     await page.route('**/api/advisor', async (route) => {
  56 |       await route.fulfill({
  57 |         status: 200,
  58 |         contentType: 'application/json',
  59 |         body: JSON.stringify({}),
  60 |       });
  61 |     });
  62 | 
  63 |     await page.goto('/dashboard');
  64 |     await expect(page).toHaveURL('/dashboard');
  65 | 
  66 |     // Check for the welcome header
> 67 |     await expect(page.getByRole('heading', { name: /dashboard/i })).toBeVisible();
     |                                                                     ^ Error: expect(locator).toBeVisible() failed
  68 |     await expect(page.getByText(/overview of your social media automation/i)).toBeVisible();
  69 | 
  70 |     // Check for the stats cards (we expect 4 cards)
  71 |     await expect(page.getByText(/total posts/i)).toBeVisible();
  72 |     await expect(page.getByText(/published/i)).toBeVisible();
  73 |     await expect(page.getByText(/scheduled/i)).toBeVisible();
  74 |     await expect(page.getByText(/connected accounts/i)).toBeVisible();
  75 | 
  76 |     // Check for the recent activity heading
  77 |     await expect(page.getByRole('heading', { name: /recent activity/i })).toBeVisible();
  78 | 
  79 |     // Check for the top posts heading
  80 |     await expect(page.getByRole('heading', { name: /top posts/i })).toBeVisible();
  81 | 
  82 |     // Check for the quick actions heading
  83 |     await expect(page.getByRole('heading', { name: /quick actions/i })).toBeVisible();
  84 | 
  85 |     // Check for at least one quick action button
  86 |     await expect(page.getByRole('link', { name: /create post/i })).toBeVisible();
  87 |   });
  88 | });
  89 | 
```