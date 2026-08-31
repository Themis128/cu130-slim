# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: login.test.ts >> Login Page — real backend >> should log in with real test user and reach dashboard
- Location: tests/login.test.ts:35:3

# Error details

```
Error: expect(page).toHaveURL(expected) failed

Expected pattern: /\/dashboard/
Received string:  "http://localhost:8082/login"
Timeout: 20000ms

Call log:
  - Expect "toHaveURL" with timeout 20000ms
    - waiting for "http://localhost:8082/login" navigation to finish...
    - navigated to "http://localhost:8082/login"
    44 × locator resolved to <html lang="en">…</html>
       - unexpected value "http://localhost:8082/login"

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
- alert
```

# Test source

```ts
  1  | import { test, expect, TEST_USER, FRONTEND_BASE, API_BASE } from './helpers/auth';
  2  | 
  3  | test.describe('Login Page — real backend', () => {
  4  |   test('should load successfully', async ({ page }) => {
  5  |     await page.goto('/login');
  6  |     await expect(page).toHaveURL('/login');
  7  |     await expect(page.getByRole('heading', { name: /welcome back/i })).toBeVisible();
  8  |   });
  9  | 
  10 |   test('should show error for invalid email', async ({ page }) => {
  11 |     await page.goto('/login');
  12 |     await page.getByRole('textbox', { name: 'Email' }).fill('invalid-email');
  13 |     await page.getByRole('textbox', { name: 'Password' }).fill('password123');
  14 |     await page.getByRole('button', { name: /sign in/i }).click();
  15 |     await expect(page.getByText(/invalid email address/i)).toBeVisible();
  16 |   });
  17 | 
  18 |   test('should show error for short password', async ({ page }) => {
  19 |     await page.goto('/login');
  20 |     await page.getByRole('textbox', { name: 'Email' }).fill('test@example.com');
  21 |     await page.getByRole('textbox', { name: 'Password' }).fill('123');
  22 |     await page.getByRole('button', { name: /sign in/i }).click();
  23 |     await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  24 |   });
  25 | 
  26 |   test('should show error for non-existent credentials', async ({ page }) => {
  27 |     await page.goto('/login');
  28 |     await page.getByRole('textbox', { name: 'Email' }).fill('nobody@example.com');
  29 |     await page.getByRole('textbox', { name: 'Password' }).fill('wrongpassword123');
  30 |     await page.getByRole('button', { name: /sign in/i }).click();
  31 |     // Real backend returns 401 with a detail message
  32 |     await expect(page.getByText(/invalid credentials/i)).toBeVisible({ timeout: 15000 });
  33 |   });
  34 | 
  35 |   test('should log in with real test user and reach dashboard', async ({ page }) => {
  36 |     await page.goto('/login');
  37 |     await page.getByRole('textbox', { name: 'Email' }).fill(TEST_USER.email);
  38 |     await page.getByRole('textbox', { name: 'Password' }).fill(TEST_USER.password);
  39 |     await page.getByRole('button', { name: /sign in/i }).click();
  40 | 
  41 |     // Real navigation to dashboard
> 42 |     await expect(page).toHaveURL(/\/dashboard/, { timeout: 20000 });
     |                        ^ Error: expect(page).toHaveURL(expected) failed
  43 |     // Dashboard greeting header renders with the real user's name
  44 |     await expect(page.getByRole('heading', { name: /good (morning|afternoon|evening)/i })).toBeVisible();
  45 |   });
  46 | });
  47 | 
```