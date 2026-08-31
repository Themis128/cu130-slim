# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reset-password.test.ts >> Reset Password Page — real backend >> should show error for short password
- Location: tests/reset-password.test.ts:41:3

# Error details

```
TimeoutError: locator.fill: Timeout 15000ms exceeded.
Call log:
  - waiting for getByRole('textbox', { name: 'New Password' })

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - generic [ref=e4]:
    - heading "Invalid Reset Link" [level=1] [ref=e5]
    - paragraph [ref=e6]: This password reset link is invalid or has expired.
    - link "Request New Reset Link" [ref=e7] [cursor=pointer]:
      - /url: /forgot-password
  - alert [ref=e8]
```

# Test source

```ts
  1  | import { test, expect, API_BASE } from './helpers/auth';
  2  | import { randomUUID } from 'crypto';
  3  | 
  4  | /**
  5  |  * Reset Password — real backend flow.
  6  |  *
  7  |  * The backend's forgot-password endpoint returns a `debug_token` in dev mode.
  8  |  * We use that token to exercise the real reset-password endpoint end-to-end.
  9  |  */
  10 | 
  11 | test.describe('Reset Password Page — real backend', () => {
  12 |   test('should show invalid link message when no token is provided', async ({ page }) => {
  13 |     await page.goto('/reset-password');
  14 |     await expect(page).toHaveURL('/reset-password');
  15 |     await expect(page.getByRole('heading', { name: /invalid reset link/i })).toBeVisible();
  16 |     await expect(page.getByText(/this password reset link is invalid or has expired/i)).toBeVisible();
  17 |     await expect(page.getByRole('link', { name: /request new reset link/i })).toBeVisible();
  18 |   });
  19 | 
  20 |   test('should show reset form when token is provided', async ({ page }) => {
  21 |     await page.goto('/reset-password?token=some-token');
  22 |     await expect(page).toHaveURL(/\/reset-password\?token=some-token/);
  23 |     await expect(page.getByRole('heading', { name: /set new password/i })).toBeVisible();
  24 |     await expect(page.getByText(/enter your new password below/i)).toBeVisible();
  25 |   });
  26 | 
  27 |   test('should show error for empty fields', async ({ page }) => {
  28 |     await page.goto('/reset-password?token=valid-token');
  29 |     await page.getByRole('button', { name: /reset password/i }).click();
  30 |     await expect(page.getByText(/all fields are required/i)).toBeVisible();
  31 |   });
  32 | 
  33 |   test('should show error for passwords not matching', async ({ page }) => {
  34 |     await page.goto('/reset-password?token=valid-token');
  35 |     await page.getByRole('textbox', { name: 'New Password' }).fill('password123');
  36 |     await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('different');
  37 |     await page.getByRole('button', { name: /reset password/i }).click();
  38 |     await expect(page.getByText(/passwords do not match/i)).toBeVisible();
  39 |   });
  40 | 
  41 |   test('should show error for short password', async ({ page }) => {
  42 |     await page.goto('/reset-password?token=valid-token');
> 43 |     await page.getByRole('textbox', { name: 'New Password' }).fill('123');
     |                                                               ^ TimeoutError: locator.fill: Timeout 15000ms exceeded.
  44 |     await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('123');
  45 |     await page.getByRole('button', { name: /reset password/i }).click();
  46 |     await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  47 |   });
  48 | 
  49 |   test('should reset password with a real debug token', async ({ request, page }) => {
  50 |     // 1. Register a fresh user so we have a known email
  51 |     const email = `reset-${randomUUID().slice(0, 8)}@social-auto.test`;
  52 |     const password = 'InitialPass-123!';
  53 |     const newPassword = 'NewResetPass-456!';
  54 | 
  55 |     await request.post(`${API_BASE}/api/v1/auth/register`, {
  56 |       data: { email, password, name: 'Reset Test' },
  57 |     });
  58 | 
  59 |     // 2. Request a reset link — the real backend returns a debug_token in dev
  60 |     const forgotRes = await request.post(`${API_BASE}/api/v1/auth/forgot-password`, {
  61 |       data: { email },
  62 |     });
  63 |     expect(forgotRes.ok()).toBeTruthy();
  64 |     const forgotBody = await forgotRes.json();
  65 |     const token = forgotBody.debug_token;
  66 |     expect(token).toBeTruthy();
  67 | 
  68 |     // 3. Use the token in the UI to reset the password
  69 |     await page.goto(`/reset-password?token=${token}`);
  70 |     await page.getByRole('textbox', { name: 'New Password' }).fill(newPassword);
  71 |     await page.getByRole('textbox', { name: 'Confirm New Password' }).fill(newPassword);
  72 |     await page.getByRole('button', { name: /reset password/i }).click();
  73 | 
  74 |     // 4. UI should show the success state
  75 |     await expect(page.getByRole('heading', { name: /password reset complete/i })).toBeVisible({ timeout: 15000 });
  76 | 
  77 |     // 5. Verify the new password actually works against the real backend
  78 |     const loginRes = await request.post(`${API_BASE}/api/v1/auth/login`, {
  79 |       form: { username: email, password: newPassword },
  80 |     });
  81 |     expect(loginRes.status()).toBe(200);
  82 |   });
  83 | 
  84 |   test('should show error for an invalid token', async ({ request, page }) => {
  85 |     // Use a clearly invalid token
  86 |     await page.goto('/reset-password?token=invalid-token-xyz');
  87 |     await page.getByRole('textbox', { name: 'New Password' }).fill('newpassword123');
  88 |     await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('newpassword123');
  89 |     await page.getByRole('button', { name: /reset password/i }).click();
  90 | 
  91 |     // Real backend returns an error for invalid tokens
  92 |     await expect(page.getByText(/invalid|expired|failed/i)).toBeVisible({ timeout: 15000 });
  93 |   });
  94 | });
  95 | 
```