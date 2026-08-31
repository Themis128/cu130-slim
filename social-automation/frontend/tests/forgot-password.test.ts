import { test, expect, TEST_USER } from './helpers/auth';

test.describe('Forgot Password Page — real backend', () => {
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
    await page.getByRole('textbox', { name: 'Email' }).fill('invalid-email');
    await page.getByRole('button', { name: /send reset link/i }).click();
    await expect(page.getByText(/invalid email address/i)).toBeVisible();
  });

  test('should show success message for a real registered email', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByRole('textbox', { name: 'Email' }).fill(TEST_USER.email);
    await page.getByRole('button', { name: /send reset link/i }).click();

    // Real backend returns 200 with "password reset email sent" toast
    await expect(page.getByText(/password reset email sent!/i)).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/check your email/i)).toBeVisible();
  });

  test('should show success message even for non-existent email (no user enumeration)', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByRole('textbox', { name: 'Email' }).fill('does-not-exist@example.com');
    await page.getByRole('button', { name: /send reset link/i }).click();

    // Real backend returns 200 with the same message to avoid enumeration
    await expect(page.getByText(/password reset email sent!/i)).toBeVisible({ timeout: 15000 });
  });

  test('should show debug token link in development for a real user', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByRole('textbox', { name: 'Email' }).fill(TEST_USER.email);
    await page.getByRole('button', { name: /send reset link/i }).click();

    // Real backend returns a debug_token in dev mode
    await expect(page.getByText(/development mode - debug token/i)).toBeVisible({ timeout: 15000 });
    // The "open reset form" button should be present
    await expect(page.getByRole('button').filter({ hasText: /external/i })).toBeVisible();
  });
});
