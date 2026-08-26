import { test, expect } from '@playwright/test';

test.describe('Register Page', () => {
  test('should load successfully', async ({ page }) => {
    await page.goto('/register');
    await expect(page).toHaveURL('/register');
    await expect(page.getByRole('heading', { name: /create your account/i })).toBeVisible();
  });

  test('should show error for empty full name', async ({ page }) => {
    await page.goto('/register');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.getByText(/full name is required/i)).toBeVisible();
  });

  test('should show error for invalid email', async ({ page }) => {
    await page.goto('/register');
    await page.getByLabelText('Full Name').fill('Test User');
    await page.getByLabelText('Email').fill('invalid-email');
    await page.getByLabelText('Password').fill('password123');
    await page.getByLabelText('Confirm Password').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.getByText(/invalid email address/i)).toBeVisible();
  });

  test('should show error for short password', async ({ page }) => {
    await page.goto('/register');
    await page.getByLabelText('Full Name').fill('Test User');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByLabelText('Password').fill('123');
    await page.getByLabelText('Confirm Password').fill('123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  });

  test('should show error for mismatched passwords', async ({ page }) => {
    await page.goto('/register');
    await page.getByLabelText('Full Name').fill('Test User');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByLabelText('Password').fill('password123');
    await page.getByLabelText('Confirm Password').fill('different');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.getByText(/passwords do not match/i)).toBeVisible();
  });

  test('should register with valid data', async ({ page }) => {
    // Mock the register API request
    await page.route('**/api/auth/register', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    await page.goto('/register');
    await page.getByLabelText('Full Name').fill('Test User');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByLabelText('Password').fill('password123');
    await page.getByLabelText('Confirm Password').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();

    // Wait for navigation to dashboard
    await expect(page).toHaveURL(/\/dashboard/);
  });

  test('should show error on failed registration', async ({ page }) => {
    // Mock the register API request to return failure
    await page.route('**/api/auth/register', async (route) => {
      await route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Email already exists' }),
      });
    });

    await page.goto('/register');
    await page.getByLabelText('Full Name').fill('Test User');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByLabelText('Password').fill('password123');
    await page.getByLabelText('Confirm Password').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();

    // Expect error toast or message
    await expect(page.getByText(/email already exists/i)).toBeVisible();
  });

  test('should show loading state during registration', async ({ page }) => {
    // Mock the register API request to delay
    await page.route('**/api/auth/register', async (route) => {
      // Return a promise that we'll resolve later
      await new Promise<void>((resolve) => {
        // We'll store the resolve function to call later
        (window as any).resolveRegister = resolve;
      });
    });

    await page.goto('/register');
    await page.getByLabelText('Full Name').fill('Test User');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByLabelText('Password').fill('password123');
    await page.getByLabelText('Confirm Password').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();

    // Check for loading state
    await expect(page.getByText(/loading.../i)).toBeVisible();
    await expect(page.getByRole('button', { name: /create account/i })).toBeDisabled();

    // Resolve the API request
    (window as any).resolveRegister();
    await page.waitForTimeout(100); // Wait for state update

    // Loading should be gone
    await expect(page.getByText(/loading.../i)).not.toBeVisible();
  });
});
