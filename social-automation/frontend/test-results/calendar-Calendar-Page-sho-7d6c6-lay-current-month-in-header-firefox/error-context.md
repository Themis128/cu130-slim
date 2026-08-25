# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: calendar.test.ts >> Calendar Page >> should display current month in header
- Location: tests/calendar.test.ts:70:3

# Error details

```
Test timeout of 30000ms exceeded while running "beforeEach" hook.
```

# Test source

```ts
  1   | import { test, expect } from '@playwright/test';
  2   | 
  3   | test.describe('Calendar Page', () => {
> 4   |   test.beforeEach(async ({ page }) => {
      |        ^ Test timeout of 30000ms exceeded while running "beforeEach" hook.
  5   |     // Mock authentication
  6   |     await page.goto('/dashboard');
  7   |     
  8   |     // Mock scheduled posts API
  9   |     await page.route('**/api/posts/scheduled', async (route) => {
  10  |       const today = new Date();
  11  |       const tomorrow = new Date(today);
  12  |       tomorrow.setDate(tomorrow.getDate() + 1);
  13  |       
  14  |       await route.fulfill({
  15  |         status: 200,
  16  |         contentType: 'application/json',
  17  |         body: JSON.stringify([
  18  |           {
  19  |             id: 'post-1',
  20  |             content_text: 'First scheduled post for today',
  21  |             scheduled_at: today.toISOString(),
  22  |             targets: [
  23  |               {
  24  |                 social_account_id: '1',
  25  |                 social_account: {
  26  |                   id: '1',
  27  |                   platform: 'linkedin',
  28  |                   username: 'testuser',
  29  |                 },
  30  |               },
  31  |             ],
  32  |           },
  33  |           {
  34  |             id: 'post-2',
  35  |             content_text: 'Another post for tomorrow',
  36  |             scheduled_at: tomorrow.toISOString(),
  37  |             targets: [
  38  |               {
  39  |                 social_account_id: '2',
  40  |                 social_account: {
  41  |                   id: '2',
  42  |                   platform: 'twitter',
  43  |                   username: 'testuser',
  44  |                 },
  45  |               },
  46  |             ],
  47  |           },
  48  |         ]),
  49  |       });
  50  |     });
  51  | 
  52  |     // Mock schedule post API
  53  |     await page.route('**/api/posts/*/schedule', async (route) => {
  54  |       await route.fulfill({
  55  |         status: 200,
  56  |         contentType: 'application/json',
  57  |         body: JSON.stringify({}),
  58  |       });
  59  |     });
  60  |   });
  61  | 
  62  |   test('should load calendar page successfully', async ({ page }) => {
  63  |     await page.goto('/calendar');
  64  |     await expect(page).toHaveURL('/calendar');
  65  |     
  66  |     // Check for main heading
  67  |     await expect(page.getByRole('heading', { name: 'Calendar' })).toBeVisible();
  68  |   });
  69  | 
  70  |   test('should display current month in header', async ({ page }) => {
  71  |     await page.goto('/calendar');
  72  |     
  73  |     // Check for current month display
  74  |     const currentMonth = new Date().toLocaleString('default', { month: 'long', year: 'numeric' });
  75  |     await expect(page.getByText(new RegExp(currentMonth, 'i'))).toBeVisible();
  76  |   });
  77  | 
  78  |   test('should display scheduled posts count', async ({ page }) => {
  79  |     await page.goto('/calendar');
  80  |     
  81  |     // Check for posts count in subtitle
  82  |     await expect(page.getByText(/scheduled post/i)).toBeVisible();
  83  |   });
  84  | 
  85  |   test('should display day of week headers', async ({ page }) => {
  86  |     await page.goto('/calendar');
  87  |     
  88  |     // Check for all weekday headers
  89  |     await expect(page.getByText('Mon')).toBeVisible();
  90  |     await expect(page.getByText('Tue')).toBeVisible();
  91  |     await expect(page.getByText('Wed')).toBeVisible();
  92  |     await expect(page.getByText('Thu')).toBeVisible();
  93  |     await expect(page.getByText('Fri')).toBeVisible();
  94  |     await expect(page.getByText('Sat')).toBeVisible();
  95  |     await expect(page.getByText('Sun')).toBeVisible();
  96  |   });
  97  | 
  98  |   test('should display calendar grid with days', async ({ page }) => {
  99  |     await page.goto('/calendar');
  100 |     
  101 |     // Check for calendar grid
  102 |     const calendarGrid = page.locator('.grid.grid-cols-7');
  103 |     await expect(calendarGrid).toBeVisible();
  104 |     
```