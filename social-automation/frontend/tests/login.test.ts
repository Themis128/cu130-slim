import { test, expect, TEST_USER } from './helpers/auth';

test.describe('Login Page — real backend', () => {
  test('should load successfully', async ({ page }) => {
    await page.goto('/login');
    await expect(page).toHaveURL('/login');
    await expect(page.getByRole('heading', { name: /welcome back/i })).toBeVisible();
  });

  test('should show error for invalid email', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('invalid-email');
    await page.getByLabel('Password').fill('password123');
    await page.getByRole('button', { name: /sign in/i }).click();
    // The Input component renders errors in a <p role="alert">
    await expect(page.getByRole('alert').filter({ hasText: /invalid email/i })).toBeVisible({ timeout: 10000 });
  });

  test('should show error for short password', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('test@example.com');
    await page.getByLabel('Password').fill('123');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByRole('alert').filter({ hasText: /password must be at least 8 characters/i })).toBeVisible({ timeout: 10000 });
  });

  test('should show error for non-existent credentials', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('nobody@example.com');
    await page.getByLabel('Password').fill('wrongpassword123');
    await page.getByRole('button', { name: /sign in/i }).click();
    // Real backend returns 401 — toast surfaces the detail
    await expect(page.getByText(/invalid credentials/i)).toBeVisible({ timeout: 15000 });
  });

  test('should log in with real test user and reach dashboard', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(TEST_USER.email);
    await page.getByLabel('Password').fill(TEST_USER.password);
    await page.getByRole('button', { name: /sign in/i }).click();

    // Real navigation to dashboard
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
    // Dashboard greeting header renders with the real user's name
    await expect(page.getByRole('heading', { name: /good (morning|afternoon|evening)/i })).toBeVisible({ timeout: 15000 });
  });
});
