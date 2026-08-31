import { test, expect, API_BASE } from './helpers/auth';
import { randomUUID } from 'crypto';

/**
 * Reset Password — real backend flow.
 *
 * The backend's forgot-password endpoint returns a `debug_token` in dev mode.
 * We use that token to exercise the real reset-password endpoint end-to-end.
 */

test.describe('Reset Password Page — real backend', () => {
  test('should show invalid link message when no token is provided', async ({ page }) => {
    await page.goto('/reset-password');
    await expect(page).toHaveURL('/reset-password');
    await expect(page.getByRole('heading', { name: /invalid reset link/i })).toBeVisible();
    await expect(page.getByText(/this password reset link is invalid or has expired/i)).toBeVisible();
    await expect(page.getByRole('link', { name: /request new reset link/i })).toBeVisible();
  });

  test('should show reset form when token is provided', async ({ page }) => {
    await page.goto('/reset-password?token=some-token');
    await expect(page).toHaveURL(/\/reset-password\?token=some-token/);
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
    await page.getByLabel('New Password').fill('password123');
    await page.getByLabel('Confirm New Password').fill('different');
    await page.getByRole('button', { name: /reset password/i }).click();
    await expect(page.getByText(/passwords do not match/i)).toBeVisible();
  });

  test('should show error for short password', async ({ page }) => {
    await page.goto('/reset-password?token=valid-token');
    await page.getByLabel('New Password').fill('123');
    await page.getByLabel('Confirm New Password').fill('123');
    await page.getByRole('button', { name: /reset password/i }).click();
    await expect(page.getByText(/password must be at least 8 characters/i)).toBeVisible();
  });

  test('should reset password with a real debug token', async ({ request, page }) => {
    // 1. Register a fresh user so we have a known email
    //    ".dev" is a real TLD; ".test" is reserved and rejected by EmailStr.
    const email = `reset-${randomUUID().slice(0, 8)}@socialauto.dev`;
    const password = 'InitialPass-123!';
    const newPassword = 'NewResetPass-456!';

    await request.post(`${API_BASE}/api/v1/auth/register`, {
      data: { email, password, name: 'Reset Test' },
    });

    // 2. Request a reset link — the real backend returns a debug_token in dev
    const forgotRes = await request.post(`${API_BASE}/api/v1/auth/forgot-password`, {
      data: { email },
    });
    expect(forgotRes.ok()).toBeTruthy();
    const forgotBody = await forgotRes.json();
    const token = forgotBody.debug_token;
    expect(token).toBeTruthy();

    // 3. Use the token in the UI to reset the password
    await page.goto(`/reset-password?token=${token}`);
    await page.getByLabel('New Password').fill(newPassword);
    await page.getByLabel('Confirm New Password').fill(newPassword);
    await page.getByRole('button', { name: /reset password/i }).click();

    // 4. UI should show the success state
    await expect(page.getByRole('heading', { name: /password reset complete/i })).toBeVisible({ timeout: 15000 });

    // 5. Verify the new password actually works against the real backend
    const loginRes = await request.post(`${API_BASE}/api/v1/auth/login`, {
      form: { username: email, password: newPassword },
    });
    expect(loginRes.status()).toBe(200);
  });

  test('should show error for an invalid token', async ({ page }) => {
    // Use a clearly invalid token
    await page.goto('/reset-password?token=invalid-token-xyz');
    await page.getByLabel('New Password').fill('newpassword123');
    await page.getByLabel('Confirm New Password').fill('newpassword123');
    await page.getByRole('button', { name: /reset password/i }).click();

    // Real backend returns an error for invalid tokens
    await expect(page.getByText(/invalid|expired|failed/i)).toBeVisible({ timeout: 15000 });
  });
});
