# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: login.test.ts >> Login Page >> should show error on failed login
- Location: tests/login.test.ts:45:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText(/invalid credentials/i)
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText(/invalid credentials/i)

```

```yaml
- heading "Welcome back" [level=3]
- paragraph: Sign in to your account to continue
- text: Email
- textbox "Email" [invalid]:
  - /placeholder: you@example.com
- alert: Email is required
- text: Password
- link "Forgot password?":
  - /url: /forgot-password
- textbox "Password":
  - /placeholder: ••••••••
  - text: wrongpassword
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
  1  | import { test, expect } from '@playwright/test';
  2  | 
  3  | test.describe('Login Page', () => {
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
  26 |   test('should log in with valid credentials', async ({ page }) => {
  27 |     // Mock the login API request
  28 |     await page.route('**/api/auth/login', async (route) => {
  29 |       await route.fulfill({
  30 |         status: 200,
  31 |         contentType: 'application/json',
  32 |         body: JSON.stringify({ ok: true }),
  33 |       });
  34 |     });
  35 | 
  36 |     await page.goto('/login');
  37 |     await page.getByRole('textbox', { name: 'Email' }).fill('test@example.com');
  38 |     await page.getByRole('textbox', { name: 'Password' }).fill('password123');
  39 |     await page.getByRole('button', { name: /sign in/i }).click();
  40 | 
  41 |     // Wait for navigation to dashboard (or the callbackUrl)
  42 |     await expect(page).toHaveURL(/\/dashboard/);
  43 |   });
  44 | 
  45 |   test('should show error on failed login', async ({ page }) => {
  46 |     // Mock the login API request to return failure
  47 |     await page.route('**/api/auth/login', async (route) => {
  48 |       await route.fulfill({
  49 |         status: 401,
  50 |         contentType: 'application/json',
  51 |         body: JSON.stringify({ detail: 'Invalid credentials' }),
  52 |       });
  53 |     });
  54 | 
  55 |     await page.goto('/login');
  56 |     await page.getByRole('textbox', { name: 'Email' }).fill('test@example.com');
  57 |     await page.getByRole('textbox', { name: 'Password' }).fill('wrongpassword');
  58 |     await page.getByRole('button', { name: /sign in/i }).click();
  59 | 
  60 |     // Expect error toast or message
> 61 |     await expect(page.getByText(/invalid credentials/i)).toBeVisible();
     |                                                          ^ Error: expect(locator).toBeVisible() failed
  62 |   });
  63 | });
  64 | 
```