import { test, expect } from '@playwright/test';

const MOCK_TOKENS = {
  access_token: 'test-access-token',
  refresh_token: 'test-refresh-token',
};

const MOCK_USER = {
  id: 'user-1',
  email: 'test@example.com',
  name: 'Test User',
};

test.describe('Login Page', () => {
  test('should load successfully', async ({ page }) => {
    await page.goto('/login');
    await expect(page).toHaveURL('/login');
    await expect(page.getByRole('heading', { name: /welcome back/i })).toBeVisible();
  });

  test('should show error for invalid email', async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('textbox', { name: 'Email' }).fill('invalid-email');
    await page.getByRole('textbox', { name: 'Password' }).fill('password123');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByText(/invalid email address/i)).toBeVisible();
  });

  test('should show error for short password', async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('textbox', { name: 'Email' }).fill('test@example.com');
    await page.getByRole('textbox', { name: 'Password' }).fill('123');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  });

  test('should log in with valid credentials', async ({ page }) => {
    // Mock the login API — actual endpoint is /api/v1/auth/login (form-encoded)
    await page.route('**/api/v1/auth/login', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_TOKENS),
      });
    });

    // Mock the /auth/me call that useAuth makes after login
    await page.route('**/api/v1/auth/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_USER),
      });
    });

    await page.goto('/login');
    await page.getByRole('textbox', { name: 'Email' }).fill('test@example.com');
    await page.getByRole('textbox', { name: 'Password' }).fill('password123');
    await page.getByRole('button', { name: /sign in/i }).click();

    // Wait for navigation to dashboard
    await expect(page).toHaveURL(/\/dashboard/);
  });

  test('should show error on failed login', async ({ page }) => {
    // Mock the login API to return 401
    await page.route('**/api/v1/auth/login', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid credentials' }),
      });
    });

    await page.goto('/login');
    await page.getByRole('textbox', { name: 'Email' }).fill('test@example.com');
    await page.getByRole('textbox', { name: 'Password' }).fill('wrongpassword');
    await page.getByRole('button', { name: /sign in/i }).click();

    // Expect error toast
    await expect(page.getByText(/invalid credentials/i)).toBeVisible();
  });

  test('should show loading state during submission', async ({ page }) => {
    // Mock the login API with a delayed response
    let resolveLogin!: () => void;
    await page.route('**/api/v1/auth/login', async (route) => {
      await new Promise<void>((resolve) => {
        resolveLogin = resolve;
      });
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_TOKENS),
      });
    });

    await page.route('**/api/v1/auth/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_USER),
      });
    });

    await page.goto('/login');
    await page.getByRole('textbox', { name: 'Email' }).fill('test@example.com');
    await page.getByRole('textbox', { name: 'Password' }).fill('password123');
    await page.getByRole('button', { name: /sign in/i }).click();

    // Button should show loading state (spinner or disabled)
    await expect(page.getByRole('button', { name: /sign in/i })).toBeDisabled();

    // Resolve the API request
    resolveLogin();
    await expect(page).toHaveURL(/\/dashboard/);
  });
});
