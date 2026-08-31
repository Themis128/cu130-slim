# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: register.test.ts >> Register Page — real backend >> should show error for short password
- Location: tests/register.test.ts:32:3

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
  - alert [ref=e26]
```

# Test source

```ts
  1  | import { test, expect } from './helpers/auth';
  2  | 
  3  | const NEW_USER = {
  4  |   email: `e2e-reg-${Date.now()}@social-auto.test`,
  5  |   password: 'E2E-Register-123!',
  6  |   name: `E2E Register ${Date.now()}`,
  7  | };
  8  | 
  9  | test.describe('Register Page — real backend', () => {
  10 |   test('should load successfully', async ({ page }) => {
  11 |     await page.goto('/register');
  12 |     await expect(page).toHaveURL('/register');
  13 |     await expect(page.getByRole('heading', { name: /create your account/i })).toBeVisible();
  14 |   });
  15 | 
  16 |   test('should show error for empty full name', async ({ page }) => {
  17 |     await page.goto('/register');
  18 |     await page.getByRole('button', { name: /create account/i }).click();
  19 |     await expect(page.getByText(/full name is required/i)).toBeVisible();
  20 |   });
  21 | 
  22 |   test('should show error for invalid email', async ({ page }) => {
  23 |     await page.goto('/register');
  24 |     await page.getByLabelText('Full Name').fill('Test User');
  25 |     await page.getByLabelText('Email').fill('invalid-email');
  26 |     await page.getByLabelText('Password').fill('password123');
  27 |     await page.getByLabelText('Confirm Password').fill('password123');
  28 |     await page.getByRole('button', { name: /create account/i }).click();
  29 |     await expect(page.getByText(/invalid email address/i)).toBeVisible();
  30 |   });
  31 | 
  32 |   test('should show error for short password', async ({ page }) => {
  33 |     await page.goto('/register');
> 34 |     await page.getByLabelText('Full Name').fill('Test User');
     |                ^ TypeError: page.getByLabelText is not a function
  35 |     await page.getByLabelText('Email').fill('test@example.com');
  36 |     await page.getByLabelText('Password').fill('123');
  37 |     await page.getByLabelText('Confirm Password').fill('123');
  38 |     await page.getByRole('button', { name: /create account/i }).click();
  39 |     await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  40 |   });
  41 | 
  42 |   test('should show error for mismatched passwords', async ({ page }) => {
  43 |     await page.goto('/register');
  44 |     await page.getByLabelText('Full Name').fill('Test User');
  45 |     await page.getByLabelText('Email').fill('test@example.com');
  46 |     await page.getByLabelText('Password').fill('password123');
  47 |     await page.getByLabelText('Confirm Password').fill('different');
  48 |     await page.getByRole('button', { name: /create account/i }).click();
  49 |     await expect(page.getByText(/passwords do not match/i)).toBeVisible();
  50 |   });
  51 | 
  52 |   test('should register a new real user and auto-login to dashboard', async ({ page }) => {
  53 |     await page.goto('/register');
  54 |     await page.getByLabelText('Full Name').fill(NEW_USER.name);
  55 |     await page.getByLabelText('Email').fill(NEW_USER.email);
  56 |     await page.getByLabelText('Password').fill(NEW_USER.password);
  57 |     await page.getByLabelText('Confirm Password').fill(NEW_USER.password);
  58 |     await page.getByRole('button', { name: /create account/i }).click();
  59 | 
  60 |     // Real auto-login flow navigates to /dashboard
  61 |     await expect(page).toHaveURL(/\/dashboard/, { timeout: 20000 });
  62 |     await expect(page.getByRole('heading', { name: /good (morning|afternoon|evening)/i })).toBeVisible();
  63 |   });
  64 | 
  65 |   test('should show error when registering an existing email', async ({ page }) => {
  66 |     // Second registration of the same email should fail at the real backend
  67 |     await page.goto('/register');
  68 |     await page.getByLabelText('Full Name').fill(NEW_USER.name);
  69 |     await page.getByLabelText('Email').fill(NEW_USER.email);
  70 |     await page.getByLabelText('Password').fill(NEW_USER.password);
  71 |     await page.getByLabelText('Confirm Password').fill(NEW_USER.password);
  72 |     await page.getByRole('button', { name: /create account/i }).click();
  73 | 
  74 |     // Real backend returns 409/422 — toast surfaces the detail
  75 |     await expect(page.getByText(/already|exists|registered/i)).toBeVisible({ timeout: 15000 });
  76 |   });
  77 | });
  78 | 
```