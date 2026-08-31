import { test, expect, API_BASE } from './helpers/auth';
import { randomUUID } from 'crypto';

/**
 * Reset Password — real backend flow.
 *
 * The backend's forgot-password endpoint returns a `debug_token` in dev mode.
 * We use that token to exercise the real reset-password endpoint end-to-end.
 *
 * Note: the ResetPasswordForm passes `error` to both Input components, so
 * error text appears in multiple alert elements. We use .first() to match.
 *
 * Note: The Input has minLength={8}, so browser-native validation prevents
 * submission of passwords shorter than 8 chars. We test the custom validation
 * by using passwords that pass native validation but fail custom checks.
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
    await expect(page.getByRole('alert').filter({ hasText: /all fields are required/i }).first()).toBeVisible({ timeout: 10000 });
  });

  test('should show error for passwords not matching', async ({ page }) => {
    await page.goto('/reset-password?token=valid-token');
    await page.locator('#password').fill('password123');
    await page.locator('#confirmPassword').fill('different123');
    await page.getByRole('button', { name: /reset password/i }).click();
    await expect(page.getByRole('alert').filter({ hasText: /passwords do not match/i }).first()).toBeVisible({ timeout: 10000 });
  });

  test('should show error for short password via native validation', async ({ page }) => {
    await page.goto('/reset-password?token=valid-token');
    await page.locator('#password').fill('123');
    await page.locator('#confirmPassword').fill('123');
    // Browser-native minLength validation prevents form submission
    // The input becomes invalid — check for the browser's validation UI
    const passwordInput = page.locator('#password');
    const isInvalid = await passwordInput.evaluate((el: HTMLInputElement) => !el.checkValidity());
    expect(isInvalid).toBe(true);
  });

  test('should reset password with a real debug token', async ({ request, page }) => {
    test.setTimeout(120_000);
    const email = `reset-${randomUUID().slice(0, 8)}@socialauto.dev`;
    const password = 'InitialPass-123!';
    const newPassword = 'NewResetPass-456!';

    const regRes = await request.post(`${API_BASE}/api/v1/auth/register`, {
      data: { email, password, name: 'Reset Test' },
      timeout: 30000,
    });
    expect(regRes.ok()).toBeTruthy();

    // Request reset link — retry to handle rate limiting and slow responses
    let token: string | null = null;
    for (let attempt = 0; attempt < 5; attempt++) {
      const forgotRes = await request.post(`${API_BASE}/api/v1/auth/forgot-password`, {
        data: { email },
        timeout: 30000,
      });
      if (forgotRes.ok()) {
        const body = await forgotRes.json();
        token = body.debug_token || null;
        if (token) break;
      }
      await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
    }
    expect(token).toBeTruthy();

    await page.goto(`/reset-password?token=${token}`);
    await page.waitForLoadState('networkidle');
    await page.locator('#password').fill(newPassword);
    await page.locator('#confirmPassword').fill(newPassword);
    await page.getByRole('button', { name: /reset password/i }).click();

    await expect(page.getByRole('heading', { name: /password reset complete/i })).toBeVisible({ timeout: 20000 });

    const loginRes = await request.post(`${API_BASE}/api/v1/auth/login`, {
      form: { username: email, password: newPassword },
      timeout: 30000,
    });
    expect(loginRes.status()).toBe(200);
  });

  test('should show error for an invalid token', async ({ page }) => {
    await page.goto('/reset-password?token=invalid-token-xyz');
    await page.locator('#password').fill('newpassword123');
    await page.locator('#confirmPassword').fill('newpassword123');
    await page.getByRole('button', { name: /reset password/i }).click();

    // The form-level error div (not role="alert") shows the backend error
    await expect(page.getByText(/invalid|expired|failed/i).first()).toBeVisible({ timeout: 20000 });
  });
});
