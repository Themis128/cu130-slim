# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: content-creation.test.ts >> Content Creation Page >> should show platform-specific character limits
- Location: tests/content-creation.test.ts:172:3

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: page.goto: net::ERR_ABORTED; maybe frame was detached?
Call log:
  - navigating to "http://localhost:3001/content/new", waiting until "load"

```

# Test source

```ts
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
> 173 |     await page.goto('/content/new');
      |                ^ Error: page.goto: net::ERR_ABORTED; maybe frame was detached?
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
  271 |     await expect(saveDraftButton).toBeVisible();
  272 |   });
  273 | 
```