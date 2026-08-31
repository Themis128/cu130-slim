# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: register.test.ts >> Register Page — real backend >> should register a new real user and auto-login to dashboard
- Location: tests/register.test.ts:53:3

# Error details

```
Error: locator.fill: Error: strict mode violation: getByLabel('Password') resolved to 2 elements:
    1) <input value="" id="password" type="password" name="password" aria-invalid="false" placeholder="••••••••" autocomplete="new-password" class="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"/> aka getByRole('textbox', { name: 'Password', exact: true })
    2) <input value="" type="password" id="confirmPassword" aria-invalid="false" placeholder="••••••••" name="confirmPassword" autocomplete="new-password" class="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"/> aka getByRole('textbox', { name: 'Confirm Password' })

Call log:
  - waiting for getByLabel('Password')

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
        - textbox "Full Name" [ref=e12]:
          - /placeholder: John Doe
          - text: E2E Register User
      - generic [ref=e13]:
        - text: Email
        - textbox "Email" [active] [ref=e15]:
          - /placeholder: you@example.com
          - text: e2e-reg-6ff35989@social-auto.test
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
  - alert [ref=e26]
```

# Test source

```ts
  1  | import { test, expect } from './helpers/auth';
  2  | import { randomUUID } from 'crypto';
  3  | 
  4  | const NEW_USER = {
  5  |   // ".dev" is a real TLD; ".test" is reserved and rejected by EmailStr (422).
  6  |   email: `e2e-reg-${randomUUID().slice(0, 8)}@socialauto.dev`,
  7  |   password: 'E2E-Register-123!',
  8  |   name: `E2E Register User`,
  9  | };
  10 | 
  11 | test.describe('Register Page — real backend', () => {
  12 |   test('should load successfully', async ({ page }) => {
  13 |     await page.goto('/register');
  14 |     await expect(page).toHaveURL('/register');
  15 |     await expect(page.getByRole('heading', { name: /create your account/i })).toBeVisible();
  16 |   });
  17 | 
  18 |   test('should show error for empty full name', async ({ page }) => {
  19 |     await page.goto('/register');
  20 |     await page.getByRole('button', { name: /create account/i }).click();
  21 |     await expect(page.getByText(/full name is required/i)).toBeVisible();
  22 |   });
  23 | 
  24 |   test('should show error for invalid email', async ({ page }) => {
  25 |     await page.goto('/register');
  26 |     await page.getByLabel('Full Name').fill('Test User');
  27 |     await page.getByLabel('Email').fill('invalid-email');
  28 |     await page.getByLabel('Password').fill('password123');
  29 |     await page.getByLabel('Confirm Password').fill('password123');
  30 |     await page.getByRole('button', { name: /create account/i }).click();
  31 |     await expect(page.getByText(/invalid email address/i)).toBeVisible();
  32 |   });
  33 | 
  34 |   test('should show error for short password', async ({ page }) => {
  35 |     await page.goto('/register');
  36 |     await page.getByLabel('Full Name').fill('Test User');
  37 |     await page.getByLabel('Email').fill('test@example.com');
  38 |     await page.getByLabel('Password').fill('123');
  39 |     await page.getByLabel('Confirm Password').fill('123');
  40 |     await page.getByRole('button', { name: /create account/i }).click();
  41 |     await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  42 |   });
  43 | 
  44 |   test('should show error for mismatched passwords', async ({ page }) => {
  45 |     await page.goto('/register');
  46 |     await page.getByLabel('Full Name').fill('Test User');
  47 |     await page.getByLabel('Email').fill('test@example.com');
  48 |     await page.getByLabel('Password').fill('password123');
  49 |     await page.getByLabel('Confirm Password').fill('different');
  50 |     await page.getByRole('button', { name: /create account/i }).click();
  51 |     await expect(page.getByText(/passwords do not match/i)).toBeVisible();
  52 |   });
  53 | 
  54 |   test('should register a new real user and auto-login to dashboard', async ({ page }) => {
  55 |     await page.goto('/register');
  56 |     await page.getByLabel('Full Name').fill(NEW_USER.name);
> 57 |     await page.getByLabel('Email').fill(NEW_USER.email);
     |                                       ^ Error: locator.fill: Error: strict mode violation: getByLabel('Password') resolved to 2 elements:
  58 |     await page.getByLabel('Password').fill(NEW_USER.password);
  59 |     await page.getByLabel('Confirm Password').fill(NEW_USER.password);
  60 |     await page.getByRole('button', { name: /create account/i }).click();
  61 | 
  62 |     // Real auto-login flow navigates to /dashboard
  63 |     await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
  64 |     await expect(page.getByRole('heading', { name: /good (morning|afternoon|evening)/i })).toBeVisible({ timeout: 15000 });
  65 |   });
  66 | 
  67 |   test('should show error when registering an existing email', async ({ page }) => {
  68 |     // Second registration of the same email should fail at the real backend
  69 |     await page.goto('/register');
  70 |     await page.getByLabel('Full Name').fill(NEW_USER.name);
  71 |     await page.getByLabel('Email').fill(NEW_USER.email);
  72 |     await page.getByLabel('Password').fill(NEW_USER.password);
  73 |     await page.getByLabel('Confirm Password').fill(NEW_USER.password);
  74 |     await page.getByRole('button', { name: /create account/i }).click();
  75 | 
  76 |     // Real backend returns 409/422 — toast surfaces the detail
  77 |     await expect(page.getByText(/already|exists|registered/i)).toBeVisible({ timeout: 15000 });
  78 |   });
  79 | });
  80 | 
```