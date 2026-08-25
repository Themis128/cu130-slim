# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reset-password.test.ts >> Reset Password Page >> should show error for short password
- Location: tests/reset-password.test.ts:33:3

# Error details

```
TimeoutError: locator.fill: Timeout 10000ms exceeded.
Call log:
  - waiting for getByRole('textbox', { name: 'New Password' })

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - generic [ref=e4]:
    - heading "Invalid Reset Link" [level=1] [ref=e5]
    - paragraph [ref=e6]: This password reset link is invalid or has expired.
    - link "Request New Reset Link" [ref=e7]:
      - /url: /forgot-password
  - button "Open Tanstack query devtools" [ref=e78] [cursor=pointer]
  - button "Open Next.js Dev Tools" [ref=e152] [cursor=pointer]
  - alert [ref=e158]
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
  22  |     await expect(page.getByText(/all fields are required/i)).toBeVisible();
  23  |   });
  24  | 
  25  |   test('should show error for passwords not matching', async ({ page }) => {
  26  |     await page.goto('/reset-password?token=valid-token');
  27  |     await page.getByRole('textbox', { name: 'New Password' }).fill('password123');
  28  |     await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('different');
  29  |     await page.getByRole('button', { name: /reset password/i }).click();
  30  |     await expect(page.getByText(/passwords do not match/i)).toBeVisible();
  31  |   });
  32  | 
  33  |   test('should show error for short password', async ({ page }) => {
  34  |     await page.goto('/reset-password?token=valid-token');
> 35  |     await page.getByRole('textbox', { name: 'New Password' }).fill('123');
      |                                                               ^ TimeoutError: locator.fill: Timeout 10000ms exceeded.
  36  |     await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('123');
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
  52  |     await page.getByRole('textbox', { name: 'New Password' }).fill('newpassword123');
  53  |     await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('newpassword123');
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
  74  |     await page.getByRole('textbox', { name: 'New Password' }).fill('newpassword123');
  75  |     await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('newpassword123');
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
  92  |     await page.getByRole('textbox', { name: 'New Password' }).fill('newpassword123');
  93  |     await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('newpassword123');
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