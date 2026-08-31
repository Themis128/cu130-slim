import { test, expect } from './helpers/auth';
import { randomUUID } from 'crypto';

const NEW_USER = {
  // ".dev" is a real TLD; ".test" is reserved and rejected by EmailStr.
  email: `e2e-reg-${randomUUID().slice(0, 8)}@socialauto.dev`,
  password: 'E2E-Register-123!',
  name: `E2E Register User`,
};

test.describe('Register Page — real backend', () => {
  test('should load successfully', async ({ page }) => {
    await page.goto('/register');
    await expect(page).toHaveURL('/register');
    await expect(page.getByRole('heading', { name: /create your account/i })).toBeVisible();
  });

  test('should show error for empty full name', async ({ page }) => {
    await page.goto('/register');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.getByRole('alert').filter({ hasText: /full name is required/i })).toBeVisible({ timeout: 10000 });
  });

  test('should show error for invalid email', async ({ page }) => {
    await page.goto('/register');
    await page.getByLabel('Full Name').fill('Test User');
    await page.getByLabel('Email').fill('invalid-email');
    // Use exact label match for Password (not Confirm Password)
    await page.locator('#password').fill('password123');
    await page.locator('#confirmPassword').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.getByRole('alert').filter({ hasText: /invalid email address/i })).toBeVisible({ timeout: 10000 });
  });

  test('should show error for short password', async ({ page }) => {
    await page.goto('/register');
    await page.getByLabel('Full Name').fill('Test User');
    await page.getByLabel('Email').fill('test@example.com');
    await page.locator('#password').fill('123');
    await page.locator('#confirmPassword').fill('123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.getByRole('alert').filter({ hasText: /password must be at least 8 characters/i })).toBeVisible({ timeout: 10000 });
  });

  test('should show error for mismatched passwords', async ({ page }) => {
    await page.goto('/register');
    await page.getByLabel('Full Name').fill('Test User');
    await page.getByLabel('Email').fill('test@example.com');
    await page.locator('#password').fill('password123');
    await page.locator('#confirmPassword').fill('different');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.getByRole('alert').filter({ hasText: /passwords do not match/i })).toBeVisible({ timeout: 10000 });
  });

  test('should register a new real user and auto-login to dashboard', async ({ page }) => {
    await page.goto('/register');
    await page.getByLabel('Full Name').fill(NEW_USER.name);
    await page.getByLabel('Email').fill(NEW_USER.email);
    await page.locator('#password').fill(NEW_USER.password);
    await page.locator('#confirmPassword').fill(NEW_USER.password);
    await page.getByRole('button', { name: /create account/i }).click();

    // Real auto-login flow navigates to /dashboard
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
    await expect(page.getByRole('heading', { name: /good (morning|afternoon|evening)/i })).toBeVisible({ timeout: 15000 });
  });

  test('should show error when registering an existing email', async ({ page }) => {
    // Second registration of the same email should fail at the real backend
    await page.goto('/register');
    await page.getByLabel('Full Name').fill(NEW_USER.name);
    await page.getByLabel('Email').fill(NEW_USER.email);
    await page.locator('#password').fill(NEW_USER.password);
    await page.locator('#confirmPassword').fill(NEW_USER.password);
    await page.getByRole('button', { name: /create account/i }).click();

    // Real backend returns 409/422 — toast surfaces the detail
    await expect(page.getByText(/already|exists|registered/i)).toBeVisible({ timeout: 15000 });
  });
});
