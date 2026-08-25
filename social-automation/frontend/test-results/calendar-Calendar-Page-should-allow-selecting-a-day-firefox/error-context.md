# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: calendar.test.ts >> Calendar Page >> should allow selecting a day
- Location: tests/calendar.test.ts:201:3

# Error details

```
TimeoutError: locator.click: Timeout 10000ms exceeded.
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
  - button "Open Tanstack query devtools" [ref=f1e74] [cursor=pointer]
  - button "Open Next.js Dev Tools" [ref=f1e129] [cursor=pointer]
  - alert [ref=f1e134]
```

# Test source

```ts
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
  204 |     // Click on a day cell
  205 |     const dayCell = page.locator('.min-h-\\[110px\\]').first();
> 206 |     await dayCell.click();
      |                   ^ TimeoutError: locator.click: Timeout 10000ms exceeded.
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
```