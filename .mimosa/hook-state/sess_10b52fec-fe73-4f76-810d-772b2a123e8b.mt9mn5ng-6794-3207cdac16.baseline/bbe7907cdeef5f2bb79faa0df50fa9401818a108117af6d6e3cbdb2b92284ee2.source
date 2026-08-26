import { test, expect } from '@playwright/test';

test.describe('Reset Password Page', () => {
  test('should show invalid link message when no token is provided', async ({ page }) => {
    await page.goto('/reset-password');
    await expect(page).toHaveURL('/reset-password');
    await expect(page.getByRole('heading', { name: /invalid reset link/i })).toBeVisible();
    await expect(page.getByText(/this password reset link is invalid or has expired/i)).toBeVisible();
    await expect(page.getByRole('link', { name: /request new reset link/i })).toBeVisible();
  });

  test('should show reset form when token is provided', async ({ page }) => {
    await page.goto('/reset-password?token=valid-token');
    await expect(page).toHaveURL('/reset-password?token=valid-token');
    await expect(page.getByRole('heading', { name: /set new password/i })).toBeVisible();
    await expect(page.getByText(/enter your new password below/i)).toBeVisible();
  });

  test('should show error for empty fields', async ({ page }) => {
    await page.goto('/reset-password?token=valid-token');
    await page.getByRole('button', { name: /reset password/i }).click();
    await expect(page.getByText(/all fields are required/i)).toBeVisible();
  });

  test('should show error for passwords not matching', async ({ page }) => {
    await page.goto('/reset-password?token=valid-token');
    await page.getByRole('textbox', { name: 'New Password' }).fill('password123');
    await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('different');
    await page.getByRole('button', { name: /reset password/i }).click();
    await expect(page.getByText(/passwords do not match/i)).toBeVisible();
  });

  test('should show error for short password', async ({ page }) => {
    await page.goto('/reset-password?token=valid-token');
    await page.getByRole('textbox', { name: 'New Password' }).fill('123');
    await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('123');
    await page.getByRole('button', { name: /reset password/i }).click();
    await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  });

  test('should show success message when password is reset', async ({ page }) => {
    // Mock the API request
    await page.route('**/api/v1/auth/reset-password', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await page.goto('/reset-password?token=valid-token');
    await page.getByRole('textbox', { name: 'New Password' }).fill('newpassword123');
    await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('newpassword123');
    await page.getByRole('button', { name: /reset password/i }).click();

    // Wait for success message (toast) and then the submitted state
    // The submitted state shows a card with a checkmark and the heading "Password Reset Complete"
    await expect(page.getByRole('heading', { name: /password reset complete/i })).toBeVisible();
    await expect(page.getByText(/your password has been successfully updated/i)).toBeVisible();
    await expect(page.getByRole('link', { name: /back to sign in/i })).toBeVisible();
  });

  test('should show error when API returns an error', async ({ page }) => {
    // Mock the API request to return 400 with a detail
    await page.route('**/api/v1/auth/reset-password', async (route) => {
      await route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid or expired token' }),
      });
    });

    await page.goto('/reset-password?token=invalid-token');
    await page.getByRole('textbox', { name: 'New Password' }).fill('newpassword123');
    await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('newpassword123');
    await page.getByRole('button', { name: /reset password/i }).click();

    // Wait for error message
    await expect(page.getByText(/invalid or expired token/i)).toBeVisible();
  });

  test('should show loading state during submission', async ({ page }) => {
    // Mock the API request to delay
    await page.route('**/api/v1/auth/reset-password', async (route) => {
      // Return a promise that we'll resolve later
      await new Promise<void>((resolve) => {
        (window as any).resolveResetPassword = resolve;
      });
    });

    await page.goto('/reset-password?token=valid-token');
    await page.getByRole('textbox', { name: 'New Password' }).fill('newpassword123');
    await page.getByRole('textbox', { name: 'Confirm New Password' }).fill('newpassword123');
    await page.getByRole('button', { name: /reset password/i }).click();

    // Check for loading state
    await expect(page.getByText(/resetting.../i)).toBeVisible();
    await expect(page.getByRole('button', { name: /reset password/i })).toBeDisabled();

    // Resolve the API request
    (window as any).resolveResetPassword();
    await page.waitForTimeout(100); // Wait for state update

    // After resolution, we expect the success message (since we mocked a 200)
    await expect(page.getByRole('heading', { name: /password reset complete/i })).toBeVisible();
  });
});
