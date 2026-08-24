import { test, expect } from '@playwright/test';

test.describe('Forgot Password Page', () => {
  test('should load successfully', async ({ page }) => {
    await page.goto('/forgot-password');
    await expect(page).toHaveURL('/forgot-password');
    await expect(page.getByRole('heading', { name: /reset your password/i })).toBeVisible();
  });

  test('should show error for empty email', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByRole('button', { name: /send reset link/i }).click();
    await expect(page.getByText(/email is required/i)).toBeVisible();
  });

  test('should show error for invalid email', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByLabelText('Email').fill('invalid-email');
    await page.getByRole('button', { name: /send reset link/i }).click();
    await expect(page.getByText(/invalid email address/i)).toBeVisible();
  });

  test('should show success message when email is submitted', async ({ page }) => {
    // Mock the API request
    await page.route('**/api/v1/auth/forgot-password', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await page.goto('/forgot-password');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByRole('button', { name: /send reset link/i }).click();

    // Wait for success message
    await expect(page.getByText(/password reset email sent!/i)).toBeVisible();
    // Check that the submitted state is shown (check for the checkmark or the heading)
    await expect(page.getByText(/check your email/i)).toBeVisible();
  });

  test('should show error when API returns 404', async ({ page }) => {
    // Mock the API request to return 404
    await page.route('**/api/v1/auth/forgot-password', async (route) => {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await page.goto('/forgot-password');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByRole('button', { name: /send reset link/i }).click();

    // Wait for error message
    await expect(page.getByText(/password reset is not yet available/i)).toBeVisible();
  });

  test('should show error when API returns other error', async ({ page }) => {
    // Mock the API request to return 400 with a detail
    await page.route('**/api/v1/auth/forgot-password', async (route) => {
      await route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Some error occurred' }),
      });
    });

    await page.goto('/forgot-password');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByRole('button', { name: /send reset link/i }).click();

    // Wait for error message
    await expect(page.getByText(/some error occurred/i)).toBeVisible();
  });

  test('should show loading state during submission', async ({ page }) => {
    // Mock the API request to delay
    await page.route('**/api/v1/auth/forgot-password', async (route) => {
      // Return a promise that we'll resolve later
      await new Promise<void>((resolve) => {
        (window as any).resolveForgotPassword = resolve;
      });
    });

    await page.goto('/forgot-password');
    await page.getByLabelText('Email').fill('test@example.com');
    await page.getByRole('button', { name: /send reset link/i }).click();

    // Check for loading state
    await expect(page.getByText(/sending.../i)).toBeVisible();
    await expect(page.getByRole('button', { name: /send reset link/i })).toBeDisabled();

    // Resolve the API request
    (window as any).resolveForgotPassword();
    await page.waitForTimeout(100); // Wait for state update

    // After resolution, we expect the success message (since we mocked a 200)
    await expect(page.getByText(/password reset email sent!/i)).toBeVisible();
  });
});
