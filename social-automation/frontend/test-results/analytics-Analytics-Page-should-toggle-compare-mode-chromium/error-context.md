# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: analytics.test.ts >> Analytics Page >> should toggle compare mode
- Location: tests/analytics.test.ts:152:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('button', { name: /compare periods/i })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByRole('button', { name: /compare periods/i })

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
  57  |         status: 200,
  58  |         contentType: 'application/json',
  59  |         body: JSON.stringify([
  60  |           {
  61  |             post_id: '1',
  62  |             content: 'This is a top performing post about our latest product launch',
  63  |             platform: 'twitter',
  64  |             likes: 150,
  65  |             comments: 25,
  66  |             shares: 10,
  67  |             published_at: new Date(Date.now() - 86400000).toISOString(),
  68  |             created_at: new Date(Date.now() - 86400000).toISOString(),
  69  |           },
  70  |           {
  71  |             post_id: '2',
  72  |             content: 'Another great post with high engagement',
  73  |             platform: 'linkedin',
  74  |             likes: 120,
  75  |             comments: 30,
  76  |             shares: 5,
  77  |             published_at: new Date(Date.now() - 172800000).toISOString(),
  78  |             created_at: new Date(Date.now() - 172800000).toISOString(),
  79  |           },
  80  |         ]),
  81  |       });
  82  |     });
  83  | 
  84  |     await page.route('**/api/engagement-trends', async (route) => {
  85  |       const days = 30;
  86  |       const data = Array.from({ length: days }, (_, i) => ({
  87  |         date: new Date(Date.now() - (days - 1 - i) * 86400000).toISOString().split('T')[0],
  88  |         value: Math.floor(Math.random() * 500) + 100,
  89  |       }));
  90  |       
  91  |       await route.fulfill({
  92  |         status: 200,
  93  |         contentType: 'application/json',
  94  |         body: JSON.stringify(data),
  95  |       });
  96  |     });
  97  |   });
  98  | 
  99  |   test('should load analytics page successfully', async ({ page }) => {
  100 |     await page.goto('/analytics');
  101 |     await expect(page).toHaveURL('/analytics');
  102 |     
  103 |     // Check for main heading
  104 |     await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible();
  105 |     await expect(page.getByText('Track your social media performance')).toBeVisible();
  106 |   });
  107 | 
  108 |   test('should display key metrics cards', async ({ page }) => {
  109 |     await page.goto('/analytics');
  110 |     
  111 |     // Check for all 4 metric cards
  112 |     await expect(page.getByText('Connected Accounts')).toBeVisible();
  113 |     await expect(page.getByText('Total Engagement')).toBeVisible();
  114 |     await expect(page.getByText('Total Followers')).toBeVisible();
  115 |     await expect(page.getByText('Posts Published')).toBeVisible();
  116 |     
  117 |     // Check that values are displayed
  118 |     await expect(page.getByText('5')).toBeVisible(); // Connected accounts
  119 |     await expect(page.getByText('12,500')).toBeVisible(); // Total engagement
  120 |     await expect(page.getByText('45,000')).toBeVisible(); // Total followers
  121 |     await expect(page.getByText('120')).toBeVisible(); // Published posts
  122 |   });
  123 | 
  124 |   test('should allow changing time range', async ({ page }) => {
  125 |     await page.goto('/analytics');
  126 |     
  127 |     // Find and click the time range selector
  128 |     const timeRangeSelect = page.getByRole('combobox').first();
  129 |     await timeRangeSelect.click();
  130 |     
  131 |     // Select different time range
  132 |     await page.getByRole('option', { name: 'Last 7 days' }).click();
  133 |     
  134 |     // Verify the selection changed
  135 |     await expect(timeRangeSelect).toHaveValue('7');
  136 |   });
  137 | 
  138 |   test('should allow filtering by platform', async ({ page }) => {
  139 |     await page.goto('/analytics');
  140 |     
  141 |     // Find the platform filter (second select)
  142 |     const platformSelect = page.locator('select').nth(1);
  143 |     await platformSelect.click();
  144 |     
  145 |     // Select specific platform
  146 |     await page.getByRole('option', { name: 'Twitter/X' }).click();
  147 |     
  148 |     // Verify the selection
  149 |     await expect(platformSelect).toHaveValue('twitter');
  150 |   });
  151 | 
  152 |   test('should toggle compare mode', async ({ page }) => {
  153 |     await page.goto('/analytics');
  154 |     
  155 |     // Find the compare button
  156 |     const compareButton = page.getByRole('button', { name: /compare periods/i });
> 157 |     await expect(compareButton).toBeVisible();
      |                                 ^ Error: expect(locator).toBeVisible() failed
  158 |     
  159 |     // Click to enable compare mode
  160 |     await compareButton.click();
  161 |     
  162 |     // Verify compare mode is active (button text changes)
  163 |     await expect(page.getByRole('button', { name: /comparing periods/i })).toBeVisible();
  164 |     
  165 |     // Verify engagement delta is shown
  166 |     await expect(page.getByText(/vs prev/i)).toBeVisible();
  167 |     
  168 |     // Click to disable compare mode
  169 |     await page.getByRole('button', { name: /comparing periods/i }).click();
  170 |     await expect(page.getByRole('button', { name: /compare periods/i })).toBeVisible();
  171 |   });
  172 | 
  173 |   test('should display engagement over time chart', async ({ page }) => {
  174 |     await page.goto('/analytics');
  175 |     
  176 |     // Check for the chart card
  177 |     await expect(page.getByRole('heading', { name: 'Engagement Over Time' })).toBeVisible();
  178 |     await expect(page.getByText('Daily engagement across all platforms')).toBeVisible();
  179 |     
  180 |     // Check that the chart area is rendered (ResponsiveContainer)
  181 |     const chartArea = page.locator('.recharts-responsive-container');
  182 |     await expect(chartArea).toBeVisible();
  183 |   });
  184 | 
  185 |   test('should display platform performance chart', async ({ page }) => {
  186 |     await page.goto('/analytics');
  187 |     
  188 |     // Check for the platform performance card
  189 |     await expect(page.getByRole('heading', { name: 'Platform Performance' })).toBeVisible();
  190 |     await expect(page.getByText('Engagement by platform')).toBeVisible();
  191 |     
  192 |     // Check that platform names are displayed in the chart
  193 |     await expect(page.getByText('twitter')).toBeVisible();
  194 |     await expect(page.getByText('linkedin')).toBeVisible();
  195 |     await expect(page.getByText('instagram')).toBeVisible();
  196 |   });
  197 | 
  198 |   test('should display impressions by platform chart', async ({ page }) => {
  199 |     await page.goto('/analytics');
  200 |     
  201 |     // Check for the impressions card
  202 |     await expect(page.getByRole('heading', { name: 'Impressions by Platform' })).toBeVisible();
  203 |     await expect(page.getByText('Total impressions per platform for the selected period')).toBeVisible();
  204 |   });
  205 | 
  206 |   test('should display platform breakdown table', async ({ page }) => {
  207 |     await page.goto('/analytics');
  208 |     
  209 |     // Check for the platform breakdown card
  210 |     await expect(page.getByRole('heading', { name: 'Platform Breakdown' })).toBeVisible();
  211 |     await expect(page.getByText('Detailed metrics per platform')).toBeVisible();
  212 |     
  213 |     // Check for table headers
  214 |     await expect(page.getByText('Platform')).toBeVisible();
  215 |     await expect(page.getByText('Impressions')).toBeVisible();
  216 |     await expect(page.getByText('Engagement')).toBeVisible();
  217 |     await expect(page.getByText('Eng. Rate')).toBeVisible();
  218 |     await expect(page.getByText('Published')).toBeVisible();
  219 |     await expect(page.getByText('Total Posts')).toBeVisible();
  220 |     
  221 |     // Check for data rows
  222 |     await expect(page.getByText('twitter')).toBeVisible();
  223 |     await expect(page.getByText('linkedin')).toBeVisible();
  224 |     await expect(page.getByText('instagram')).toBeVisible();
  225 |   });
  226 | 
  227 |   test('should display top performing posts', async ({ page }) => {
  228 |     await page.goto('/analytics');
  229 |     
  230 |     // Check for the top posts card
  231 |     await expect(page.getByRole('heading', { name: 'Top Performing Posts' })).toBeVisible();
  232 |     await expect(page.getByText('Your best content by engagement')).toBeVisible();
  233 |     
  234 |     // Check for post entries
  235 |     await expect(page.getByText('#1')).toBeVisible();
  236 |     await expect(page.getByText('#2')).toBeVisible();
  237 |     
  238 |     // Check for post content snippets
  239 |     await expect(page.getByText(/top performing post/i)).toBeVisible();
  240 |   });
  241 | 
  242 |   test('should show empty state when no top posts data', async ({ page }) => {
  243 |     // Mock empty top posts response
  244 |     await page.route('**/api/top-posts', async (route) => {
  245 |       await route.fulfill({
  246 |         status: 200,
  247 |         contentType: 'application/json',
  248 |         body: JSON.stringify([]),
  249 |       });
  250 |     });
  251 |     
  252 |     await page.goto('/analytics');
  253 |     
  254 |     // Check for empty state
  255 |     await expect(page.getByText('No data for this period')).toBeVisible();
  256 |     await expect(page.getByText('Publish posts to start seeing engagement metrics')).toBeVisible();
  257 |     await expect(page.getByRole('link', { name: 'Create a post' })).toBeVisible();
```