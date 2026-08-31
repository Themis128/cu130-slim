# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: forgot-password.test.ts >> Forgot Password Page — real backend >> should show debug token link in development for a real user
- Location: tests/forgot-password.test.ts:42:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText(/development mode - debug token/i)
Expected: visible
Timeout: 15000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 15000ms
  - waiting for getByText(/development mode - debug token/i)

```

```yaml
- img
- heading "This page couldn’t load" [level=1]
- paragraph: Reload to try again, or go back.
- button "Reload"
- button "Back"
```

# Test source

```ts
  1  | import { test, expect, TEST_USER } from './helpers/auth';
  2  | 
  3  | test.describe('Forgot Password Page — real backend', () => {
  4  |   test('should load successfully', async ({ page }) => {
  5  |     await page.goto('/forgot-password');
  6  |     await expect(page).toHaveURL('/forgot-password');
  7  |     await expect(page.getByRole('heading', { name: /reset your password/i })).toBeVisible();
  8  |   });
  9  | 
  10 |   test('should show error for empty email', async ({ page }) => {
  11 |     await page.goto('/forgot-password');
  12 |     await page.getByRole('button', { name: /send reset link/i }).click();
  13 |     await expect(page.getByText(/email is required/i)).toBeVisible();
  14 |   });
  15 | 
  16 |   test('should show error for invalid email', async ({ page }) => {
  17 |     await page.goto('/forgot-password');
  18 |     await page.getByRole('textbox', { name: 'Email' }).fill('invalid-email');
  19 |     await page.getByRole('button', { name: /send reset link/i }).click();
  20 |     await expect(page.getByText(/invalid email address/i)).toBeVisible();
  21 |   });
  22 | 
  23 |   test('should show success message for a real registered email', async ({ page }) => {
  24 |     await page.goto('/forgot-password');
  25 |     await page.getByRole('textbox', { name: 'Email' }).fill(TEST_USER.email);
  26 |     await page.getByRole('button', { name: /send reset link/i }).click();
  27 | 
  28 |     // Real backend returns 200 with "password reset email sent" toast
  29 |     await expect(page.getByText(/password reset email sent!/i)).toBeVisible({ timeout: 15000 });
  30 |     await expect(page.getByText(/check your email/i)).toBeVisible();
  31 |   });
  32 | 
  33 |   test('should show success message even for non-existent email (no user enumeration)', async ({ page }) => {
  34 |     await page.goto('/forgot-password');
  35 |     await page.getByRole('textbox', { name: 'Email' }).fill('does-not-exist@example.com');
  36 |     await page.getByRole('button', { name: /send reset link/i }).click();
  37 | 
  38 |     // Real backend returns 200 with the same message to avoid enumeration
  39 |     await expect(page.getByText(/password reset email sent!/i)).toBeVisible({ timeout: 15000 });
  40 |   });
  41 | 
  42 |   test('should show debug token link in development for a real user', async ({ page }) => {
  43 |     await page.goto('/forgot-password');
  44 |     await page.getByRole('textbox', { name: 'Email' }).fill(TEST_USER.email);
  45 |     await page.getByRole('button', { name: /send reset link/i }).click();
  46 | 
  47 |     // Real backend returns a debug_token in dev mode
> 48 |     await expect(page.getByText(/development mode - debug token/i)).toBeVisible({ timeout: 15000 });
     |                                                                     ^ Error: expect(locator).toBeVisible() failed
  49 |     // The "open reset form" button should be present
  50 |     await expect(page.getByRole('button').filter({ hasText: /external/i })).toBeVisible();
  51 |   });
  52 | });
  53 | 
```