# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: calendar.test.ts >> Calendar Page >> should display calendar grid with days
- Location: tests/calendar.test.ts:98:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('.grid.grid-cols-7')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for locator('.grid.grid-cols-7')

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
  3   | test.describe('Calendar Page', () => {
  4   |   test.beforeEach(async ({ page }) => {
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
> 103 |     await expect(calendarGrid).toBeVisible();
      |                                ^ Error: expect(locator).toBeVisible() failed
  104 |     
  105 |     // Check for day cells (should have at least 28 days)
  106 |     const dayCells = page.locator('.min-h-\\[110px\\]');
  107 |     await expect(dayCells.first()).toBeVisible();
  108 |   });
  109 | 
  110 |   test('should highlight today', async ({ page }) => {
  111 |     await page.goto('/calendar');
  112 |     
  113 |     // Check for today's highlight (should have primary background)
  114 |     const today = new Date().getDate();
  115 |     const todayCell = page.getByText(today.toString());
  116 |     await expect(todayCell.first()).toBeVisible();
  117 |   });
  118 | 
  119 |   test('should display scheduled posts as chips', async ({ page }) => {
  120 |     await page.goto('/calendar');
  121 |     
  122 |     // Check for post chips (should show content snippet)
  123 |     await expect(page.getByText(/scheduled post/i)).toBeVisible();
  124 |   });
  125 | 
  126 |   test('should show platform indicators on post chips', async ({ page }) => {
  127 |     await page.goto('/calendar');
  128 |     
  129 |     // Check for platform color indicators
  130 |     const platformIndicators = page.locator('.rounded-full');
  131 |     await expect(platformIndicators.first()).toBeVisible();
  132 |   });
  133 | 
  134 |   test('should allow navigation to previous month', async ({ page }) => {
  135 |     await page.goto('/calendar');
  136 |     
  137 |     // Get current month text
  138 |     const currentMonthText = await page.getByRole('button', { name: /\w+ \d{4}/ }).textContent();
  139 |     
  140 |     // Click previous month button
  141 |     const prevButton = page.getByRole('button').filter({ hasText: '' }).nth(0);
  142 |     await prevButton.click();
  143 |     
  144 |     // Wait for navigation to complete
  145 |     await page.waitForTimeout(500);
  146 |     
  147 |     // Check that month changed (this is a basic check)
  148 |     await expect(page.getByRole('button', { name: /\w+ \d{4}/ })).toBeVisible();
  149 |   });
  150 | 
  151 |   test('should allow navigation to next month', async ({ page }) => {
  152 |     await page.goto('/calendar');
  153 |     
  154 |     // Click next month button
  155 |     const nextButton = page.getByRole('button').filter({ hasText: '' }).nth(1);
  156 |     await nextButton.click();
  157 |     
  158 |     // Wait for navigation to complete
  159 |     await page.waitForTimeout(500);
  160 |     
  161 |     // Check that month changed
  162 |     await expect(page.getByRole('button', { name: /\w+ \d{4}/ })).toBeVisible();
  163 |   });
  164 | 
  165 |   test('should allow returning to current month', async ({ page }) => {
  166 |     await page.goto('/calendar');
  167 |     
  168 |     // Navigate away first
  169 |     const nextButton = page.getByRole('button').filter({ hasText: '' }).nth(1);
  170 |     await nextButton.click();
  171 |     await page.waitForTimeout(500);
  172 |     
  173 |     // Click current month button
  174 |     const currentMonthButton = page.getByRole('button', { name: /\w+ \d{4}/ });
  175 |     await currentMonthButton.click();
  176 |     
  177 |     // Should return to current month
  178 |     const currentMonth = new Date().toLocaleString('default', { month: 'long', year: 'numeric' });
  179 |     await expect(page.getByText(new RegExp(currentMonth, 'i'))).toBeVisible();
  180 |   });
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
```