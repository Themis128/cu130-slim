# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reset-password.test.ts >> Reset Password Page >> should show error for empty fields
- Location: tests/reset-password.test.ts:19:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText(/all fields are required/i)
Expected: visible
Error: strict mode violation: getByText(/all fields are required/i) resolved to 3 elements:
    1) <p role="alert" id="password-error" class="mt-1.5 text-sm text-destructive">All fields are required</p> aka locator('#password-error')
    2) <p role="alert" id="confirmPassword-error" class="mt-1.5 text-sm text-destructive">All fields are required</p> aka locator('#confirmPassword-error')
    3) <div class="text-sm text-red-600 flex items-center gap-2">…</div> aka getByText('All fields are required').nth(2)

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText(/all fields are required/i)

```

# Page snapshot

```yaml
- generic [ref=e1]:
  - generic [ref=e4]:
    - generic [ref=e5]:
      - heading "Set New Password" [level=3] [ref=e6]
      - paragraph [ref=e7]: Enter your new password below
    - generic [ref=e9]:
      - generic [ref=e10]:
        - text: New Password
        - generic [ref=e15]:
          - textbox "New Password" [invalid] [ref=e16]:
            - /placeholder: Enter new password
          - alert [ref=e17]: All fields are required
      - generic [ref=e18]:
        - text: Confirm New Password
        - generic [ref=e23]:
          - textbox "Confirm New Password" [invalid] [ref=e24]:
            - /placeholder: Confirm new password
          - alert [ref=e25]: All fields are required
      - generic [ref=e26]: All fields are required
      - button "Reset Password" [active] [ref=e29] [cursor=pointer]
    - paragraph [ref=e31]:
      - text: Remembered your password?
      - link "Sign in" [ref=e32]:
        - /url: /login
  - button "Open Tanstack query devtools" [ref=e103] [cursor=pointer]
  - alert [ref=e172]
```

# Test source

```ts
  1   | import { test, expect } from '@playwright/test';
  2   | 
  3   | test.describe('Reset Password Page', () => {
  4   |   test('should show invalid link message when no token is provided', async ({ page }) => {
  5   |     await page.goto('/reset-password');
  6   |     await expect(page).toHaveURL('/reset-password');
  7   |     await expect(page.getByRole('heading', { name: /invalid reset link/i })).toBeVisible();
  8   |     await expect(page.getByText(/this password reset link is invalid or has expired/i)).toBeVisible();
  9   |     await expect(page.getByRole('link', { name: /request new reset link/i })).toBeVisible();
  10  |   });
  11  | 
  12  |   test('should show reset form when token is provided', async ({ page }) => {
  13  |     await page.goto('/reset-password?token=valid-token');
  14  |     await expect(page).toHaveURL('/reset-password?token=valid-token');
  15  |     await expect(page.getByRole('heading', { name: /set new password/i })).toBeVisible();
  16  |     await expect(page.getByText(/enter your new password below/i)).toBeVisible();
  17  |   });
  18  | 
  19  |   test('should show error for empty fields', async ({ page }) => {
  20  |     await page.goto('/reset-password?token=valid-token');
  21  |     await page.getByRole('button', { name: /reset password/i }).click();
> 22  |     await expect(page.getByText(/all fields are required/i)).toBeVisible();
      |                                                              ^ Error: expect(locator).toBeVisible() failed
  23  |   });
  24  | 
  25  |   test('should show error for passwords not matching', async ({ page }) => {
  26  |     await page.goto('/reset-password?token=valid-token');
  27  |     await page.getByLabelText('New Password').fill('password123');
  28  |     await page.getByLabelText('Confirm New Password').fill('different');
  29  |     await page.getByRole('button', { name: /reset password/i }).click();
  30  |     await expect(page.getByText(/passwords do not match/i)).toBeVisible();
  31  |   });
  32  | 
  33  |   test('should show error for short password', async ({ page }) => {
  34  |     await page.goto('/reset-password?token=valid-token');
  35  |     await page.getByLabelText('New Password').fill('123');
  36  |     await page.getByLabelText('Confirm New Password').fill('123');
  37  |     await page.getByRole('button', { name: /reset password/i }).click();
  38  |     await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  39  |   });
  40  | 
  41  |   test('should show success message when password is reset', async ({ page }) => {
  42  |     // Mock the API request
  43  |     await page.route('**/api/v1/auth/reset-password', async (route) => {
  44  |       await route.fulfill({
  45  |         status: 200,
  46  |         contentType: 'application/json',
  47  |         body: JSON.stringify({}),
  48  |       });
  49  |     });
  50  | 
  51  |     await page.goto('/reset-password?token=valid-token');
  52  |     await page.getByLabelText('New Password').fill('newpassword123');
  53  |     await page.getByLabelText('Confirm New Password').fill('newpassword123');
  54  |     await page.getByRole('button', { name: /reset password/i }).click();
  55  | 
  56  |     // Wait for success message (toast) and then the submitted state
  57  |     // The submitted state shows a card with a checkmark and the heading "Password Reset Complete"
  58  |     await expect(page.getByRole('heading', { name: /password reset complete/i })).toBeVisible();
  59  |     await expect(page.getByText(/your password has been successfully updated/i)).toBeVisible();
  60  |     await expect(page.getByRole('link', { name: /back to sign in/i })).toBeVisible();
  61  |   });
  62  | 
  63  |   test('should show error when API returns an error', async ({ page }) => {
  64  |     // Mock the API request to return 400 with a detail
  65  |     await page.route('**/api/v1/auth/reset-password', async (route) => {
  66  |       await route.fulfill({
  67  |         status: 400,
  68  |         contentType: 'application/json',
  69  |         body: JSON.stringify({ detail: 'Invalid or expired token' }),
  70  |       });
  71  |     });
  72  | 
  73  |     await page.goto('/reset-password?token=invalid-token');
  74  |     await page.getByLabelText('New Password').fill('newpassword123');
  75  |     await page.getByLabelText('Confirm New Password').fill('newpassword123');
  76  |     await page.getByRole('button', { name: /reset password/i }).click();
  77  | 
  78  |     // Wait for error message
  79  |     await expect(page.getByText(/invalid or expired token/i)).toBeVisible();
  80  |   });
  81  | 
  82  |   test('should show loading state during submission', async ({ page }) => {
  83  |     // Mock the API request to delay
  84  |     await page.route('**/api/v1/auth/reset-password', async (route) => {
  85  |       // Return a promise that we'll resolve later
  86  |       await new Promise<void>((resolve) => {
  87  |         (window as any).resolveResetPassword = resolve;
  88  |       });
  89  |     });
  90  | 
  91  |     await page.goto('/reset-password?token=valid-token');
  92  |     await page.getByLabelText('New Password').fill('newpassword123');
  93  |     await page.getByLabelText('Confirm New Password').fill('newpassword123');
  94  |     await page.getByRole('button', { name: /reset password/i }).click();
  95  | 
  96  |     // Check for loading state
  97  |     await expect(page.getByText(/resetting.../i)).toBeVisible();
  98  |     await expect(page.getByRole('button', { name: /reset password/i })).toBeDisabled();
  99  | 
  100 |     // Resolve the API request
  101 |     (window as any).resolveResetPassword();
  102 |     await page.waitForTimeout(100); // Wait for state update
  103 | 
  104 |     // After resolution, we expect the success message (since we mocked a 200)
  105 |     await expect(page.getByRole('heading', { name: /password reset complete/i })).toBeVisible();
  106 |   });
  107 | });
  108 | 
```