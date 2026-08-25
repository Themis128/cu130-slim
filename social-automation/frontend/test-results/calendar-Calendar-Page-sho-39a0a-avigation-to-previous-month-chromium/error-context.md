# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: calendar.test.ts >> Calendar Page >> should allow navigation to previous month
- Location: tests/calendar.test.ts:134:3

# Error details

```
TimeoutError: locator.textContent: Timeout 10000ms exceeded.
Call log:
  - waiting for getByRole('button', { name: /\w+ \d{4}/ })

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
  - button "Open Next.js Dev Tools" [ref=f1e127] [cursor=pointer]
  - alert [ref=f1e131]
```

# Test source

```ts
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
> 138 |     const currentMonthText = await page.getByRole('button', { name: /\w+ \d{4}/ }).textContent();
      |                                                                                    ^ TimeoutError: locator.textContent: Timeout 10000ms exceeded.
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
```