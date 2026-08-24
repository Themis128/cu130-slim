# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: forgot-password.test.ts >> Forgot Password Page >> should show error when API returns other error
- Location: tests/forgot-password.test.ts:61:3

# Error details

```
TypeError: page.getByLabelText is not a function
```

# Page snapshot

```yaml
- generic [ref=e4]:
  - generic [ref=e5]:
    - heading "Reset your password" [level=3] [ref=e6]
    - paragraph [ref=e7]: Enter your email and we'll send you a reset link
  - generic [ref=e9]:
    - generic [ref=e10]:
      - text: Email
      - textbox "Email" [active] [ref=e16]:
        - /placeholder: you@example.com
    - button "Send reset link" [ref=e17] [cursor=pointer]
  - paragraph [ref=e19]:
    - text: Remembered your password?
    - link "Sign in" [ref=e20] [cursor=pointer]:
      - /url: /login
```

# Test source

```ts
  1   | import { test, expect } from '@playwright/test';
  2   | 
  3   | test.describe('Forgot Password Page', () => {
  4   |   test('should load successfully', async ({ page }) => {
  5   |     await page.goto('/forgot-password');
  6   |     await expect(page).toHaveURL('/forgot-password');
  7   |     await expect(page.getByRole('heading', { name: /reset your password/i })).toBeVisible();
  8   |   });
  9   | 
  10  |   test('should show error for empty email', async ({ page }) => {
  11  |     await page.goto('/forgot-password');
  12  |     await page.getByRole('button', { name: /send reset link/i }).click();
  13  |     await expect(page.getByText(/email is required/i)).toBeVisible();
  14  |   });
  15  | 
  16  |   test('should show error for invalid email', async ({ page }) => {
  17  |     await page.goto('/forgot-password');
  18  |     await page.getByLabelText('Email').fill('invalid-email');
  19  |     await page.getByRole('button', { name: /send reset link/i }).click();
  20  |     await expect(page.getByText(/invalid email address/i)).toBeVisible();
  21  |   });
  22  | 
  23  |   test('should show success message when email is submitted', async ({ page }) => {
  24  |     // Mock the API request
  25  |     await page.route('**/api/v1/auth/forgot-password', async (route) => {
  26  |       await route.fulfill({
  27  |         status: 200,
  28  |         contentType: 'application/json',
  29  |         body: JSON.stringify({}),
  30  |       });
  31  |     });
  32  | 
  33  |     await page.goto('/forgot-password');
  34  |     await page.getByLabelText('Email').fill('test@example.com');
  35  |     await page.getByRole('button', { name: /send reset link/i }).click();
  36  | 
  37  |     // Wait for success message
  38  |     await expect(page.getByText(/password reset email sent!/i)).toBeVisible();
  39  |     // Check that the submitted state is shown (check for the checkmark or the heading)
  40  |     await expect(page.getByText(/check your email/i)).toBeVisible();
  41  |   });
  42  | 
  43  |   test('should show error when API returns 404', async ({ page }) => {
  44  |     // Mock the API request to return 404
  45  |     await page.route('**/api/v1/auth/forgot-password', async (route) => {
  46  |       await route.fulfill({
  47  |         status: 404,
  48  |         contentType: 'application/json',
  49  |         body: JSON.stringify({}),
  50  |       });
  51  |     });
  52  | 
  53  |     await page.goto('/forgot-password');
  54  |     await page.getByLabelText('Email').fill('test@example.com');
  55  |     await page.getByRole('button', { name: /send reset link/i }).click();
  56  | 
  57  |     // Wait for error message
  58  |     await expect(page.getByText(/password reset is not yet available/i)).toBeVisible();
  59  |   });
  60  | 
  61  |   test('should show error when API returns other error', async ({ page }) => {
  62  |     // Mock the API request to return 400 with a detail
  63  |     await page.route('**/api/v1/auth/forgot-password', async (route) => {
  64  |       await route.fulfill({
  65  |         status: 400,
  66  |         contentType: 'application/json',
  67  |         body: JSON.stringify({ detail: 'Some error occurred' }),
  68  |       });
  69  |     });
  70  | 
  71  |     await page.goto('/forgot-password');
> 72  |     await page.getByLabelText('Email').fill('test@example.com');
      |                ^ TypeError: page.getByLabelText is not a function
  73  |     await page.getByRole('button', { name: /send reset link/i }).click();
  74  | 
  75  |     // Wait for error message
  76  |     await expect(page.getByText(/some error occurred/i)).toBeVisible();
  77  |   });
  78  | 
  79  |   test('should show loading state during submission', async ({ page }) => {
  80  |     // Mock the API request to delay
  81  |     await page.route('**/api/v1/auth/forgot-password', async (route) => {
  82  |       // Return a promise that we'll resolve later
  83  |       await new Promise<void>((resolve) => {
  84  |         (window as any).resolveForgotPassword = resolve;
  85  |       });
  86  |     });
  87  | 
  88  |     await page.goto('/forgot-password');
  89  |     await page.getByLabelText('Email').fill('test@example.com');
  90  |     await page.getByRole('button', { name: /send reset link/i }).click();
  91  | 
  92  |     // Check for loading state
  93  |     await expect(page.getByText(/sending.../i)).toBeVisible();
  94  |     await expect(page.getByRole('button', { name: /send reset link/i })).toBeDisabled();
  95  | 
  96  |     // Resolve the API request
  97  |     (window as any).resolveForgotPassword();
  98  |     await page.waitForTimeout(100); // Wait for state update
  99  | 
  100 |     // After resolution, we expect the success message (since we mocked a 200)
  101 |     await expect(page.getByText(/password reset email sent!/i)).toBeVisible();
  102 |   });
  103 | });
  104 | 
```