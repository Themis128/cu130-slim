import { test, expect } from '@playwright/test';

test.describe('Login Page', () => {
  test('should load successfully', async ({ page }) => {
    await page.goto('/login');
    await expect(page).toHaveURL('/login');
    await expect(page.getByRole('heading', { name: /welcome back/i })).toBeVisible();
  });

  test('should show error for invalid email', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabelText('Email').fill('invalid-email');
    await page.getByLabelText('Password').fill('password123');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByText(/invalid email address/i)).toBeVisible();
  });

  test('should show error for short password', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByLabelText('Password').fill('123');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  });

  test('should log in with valid credentials', async ({ page }) => {
    // Mock the login API request
    await page.route('**/api/auth/login', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    await page.goto('/login');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByLabelText('Password').fill('password123');
    await page.getByRole('button', { name: /sign in/i }).click();

    // Wait for navigation to dashboard (or the callbackUrl)
    await expect(page).toHaveURL(/\/dashboard/);
  });

  test('should show error on failed login', async ({ page }) => {
    // Mock the login API request to return failure
    await page.route('**/api/auth/login', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid credentials' }),
      });
    });

    await page.goto('/login');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByLabelText('Password').fill('wrongpassword');
    await page.getByRole('button', { name: /sign in/i }).click();

    // Expect error toast or message
    await expect(page.getByText(/invalid credentials/i)).toBeVisible();
  });
});
