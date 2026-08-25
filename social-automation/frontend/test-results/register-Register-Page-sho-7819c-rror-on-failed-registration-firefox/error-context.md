# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: register.test.ts >> Register Page >> should show error on failed registration
- Location: tests/register.test.ts:67:3

# Error details

```
TypeError: page.getByLabelText is not a function
```

# Page snapshot

```yaml
- generic [ref=e1]:
  - generic [ref=e4]:
    - generic [ref=e5]:
      - heading "Create your account" [level=3] [ref=e6]
      - paragraph [ref=e7]: Start automating your social media today
    - generic [ref=e9]:
      - generic [ref=e10]:
        - text: Full Name
        - textbox "Full Name" [active] [ref=e12]:
          - /placeholder: John Doe
      - generic [ref=e13]:
        - text: Email
        - textbox "Email" [ref=e15]:
          - /placeholder: you@example.com
      - generic [ref=e16]:
        - text: Password
        - textbox "Password" [ref=e18]:
          - /placeholder: ••••••••
      - generic [ref=e19]:
        - text: Confirm Password
        - textbox "Confirm Password" [ref=e21]:
          - /placeholder: ••••••••
      - button "Create account" [ref=e22] [cursor=pointer]
    - paragraph [ref=e24]:
      - text: Already have an account?
      - link "Sign in" [ref=e25] [cursor=pointer]:
        - /url: /login
  - button "Open Tanstack query devtools" [ref=e77] [cursor=pointer]
  - button "Open Next.js Dev Tools" [ref=e132] [cursor=pointer]
  - alert [ref=e137]
```

# Test source

```ts
  1   | import { test, expect } from '@playwright/test';
  2   | 
  3   | test.describe('Register Page', () => {
  4   |   test('should load successfully', async ({ page }) => {
  5   |     await page.goto('/register');
  6   |     await expect(page).toHaveURL('/register');
  7   |     await expect(page.getByRole('heading', { name: /create your account/i })).toBeVisible();
  8   |   });
  9   | 
  10  |   test('should show error for empty full name', async ({ page }) => {
  11  |     await page.goto('/register');
  12  |     await page.getByRole('button', { name: /create account/i }).click();
  13  |     await expect(page.getByText(/full name is required/i)).toBeVisible();
  14  |   });
  15  | 
  16  |   test('should show error for invalid email', async ({ page }) => {
  17  |     await page.goto('/register');
  18  |     await page.getByLabelText('Full Name').fill('Test User');
  19  |     await page.getByLabelText('Email').fill('invalid-email');
  20  |     await page.getByLabelText('Password').fill('password123');
  21  |     await page.getByLabelText('Confirm Password').fill('password123');
  22  |     await page.getByRole('button', { name: /create account/i }).click();
  23  |     await expect(page.getByText(/invalid email address/i)).toBeVisible();
  24  |   });
  25  | 
  26  |   test('should show error for short password', async ({ page }) => {
  27  |     await page.goto('/register');
  28  |     await page.getByLabelText('Full Name').fill('Test User');
  29  |     await page.getByLabelText('Email').fill('test@example.com');
  30  |     await page.getByLabelText('Password').fill('123');
  31  |     await page.getByLabelText('Confirm Password').fill('123');
  32  |     await page.getByRole('button', { name: /create account/i }).click();
  33  |     await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  34  |   });
  35  | 
  36  |   test('should show error for mismatched passwords', async ({ page }) => {
  37  |     await page.goto('/register');
  38  |     await page.getByLabelText('Full Name').fill('Test User');
  39  |     await page.getByLabelText('Email').fill('test@example.com');
  40  |     await page.getByLabelText('Password').fill('password123');
  41  |     await page.getByLabelText('Confirm Password').fill('different');
  42  |     await page.getByRole('button', { name: /create account/i }).click();
  43  |     await expect(page.getByText(/passwords do not match/i)).toBeVisible();
  44  |   });
  45  | 
  46  |   test('should register with valid data', async ({ page }) => {
  47  |     // Mock the register API request
  48  |     await page.route('**/api/auth/register', async (route) => {
  49  |       await route.fulfill({
  50  |         status: 200,
  51  |         contentType: 'application/json',
  52  |         body: JSON.stringify({ ok: true }),
  53  |       });
  54  |     });
  55  | 
  56  |     await page.goto('/register');
  57  |     await page.getByLabelText('Full Name').fill('Test User');
  58  |     await page.getByLabelText('Email').fill('test@example.com');
  59  |     await page.getByLabelText('Password').fill('password123');
  60  |     await page.getByLabelText('Confirm Password').fill('password123');
  61  |     await page.getByRole('button', { name: /create account/i }).click();
  62  | 
  63  |     // Wait for navigation to dashboard
  64  |     await expect(page).toHaveURL(/\/dashboard/);
  65  |   });
  66  | 
  67  |   test('should show error on failed registration', async ({ page }) => {
  68  |     // Mock the register API request to return failure
  69  |     await page.route('**/api/auth/register', async (route) => {
  70  |       await route.fulfill({
  71  |         status: 400,
  72  |         contentType: 'application/json',
  73  |         body: JSON.stringify({ detail: 'Email already exists' }),
  74  |       });
  75  |     });
  76  | 
  77  |     await page.goto('/register');
> 78  |     await page.getByLabelText('Full Name').fill('Test User');
      |                ^ TypeError: page.getByLabelText is not a function
  79  |     await page.getByLabelText('Email').fill('test@example.com');
  80  |     await page.getByLabelText('Password').fill('password123');
  81  |     await page.getByLabelText('Confirm Password').fill('password123');
  82  |     await page.getByRole('button', { name: /create account/i }).click();
  83  | 
  84  |     // Expect error toast or message
  85  |     await expect(page.getByText(/email already exists/i)).toBeVisible();
  86  |   });
  87  | 
  88  |   test('should show loading state during registration', async ({ page }) => {
  89  |     // Mock the register API request to delay
  90  |     await page.route('**/api/auth/register', async (route) => {
  91  |       // Return a promise that we'll resolve later
  92  |       await new Promise<void>((resolve) => {
  93  |         // We'll store the resolve function to call later
  94  |         (window as any).resolveRegister = resolve;
  95  |       });
  96  |     });
  97  | 
  98  |     await page.goto('/register');
  99  |     await page.getByLabelText('Full Name').fill('Test User');
  100 |     await page.getByLabelText('Email').fill('test@example.com');
  101 |     await page.getByLabelText('Password').fill('password123');
  102 |     await page.getByLabelText('Confirm Password').fill('password123');
  103 |     await page.getByRole('button', { name: /create account/i }).click();
  104 | 
  105 |     // Check for loading state
  106 |     await expect(page.getByText(/loading.../i)).toBeVisible();
  107 |     await expect(page.getByRole('button', { name: /create account/i })).toBeDisabled();
  108 | 
  109 |     // Resolve the API request
  110 |     (window as any).resolveRegister();
  111 |     await page.waitForTimeout(100); // Wait for state update
  112 | 
  113 |     // Loading should be gone
  114 |     await expect(page.getByText(/loading.../i)).not.toBeVisible();
  115 |   });
  116 | });
  117 | 
```