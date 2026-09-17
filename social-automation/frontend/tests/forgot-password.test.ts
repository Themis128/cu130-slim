import { test, expect, TEST_USER, API_BASE } from './helpers/auth';

test.describe('Forgot Password Page — real backend', () => {
  test('should load successfully', async ({ page }) => {
    await page.goto('/forgot-password');
    await expect(page).toHaveURL('/forgot-password');
    await expect(page.getByRole('heading', { name: /reset your password/i })).toBeVisible();
  });

  test('should show error for empty email', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByRole('button', { name: /send reset link/i }).click();
    // The forgot-password page uses a custom error state (not the Input error prop)
    // It renders error text in a <p role="alert"> via the Input component
    await expect(page.getByRole('alert').filter({ hasText: /email is required/i })).toBeVisible({ timeout: 10000 });
  });

  test('should show error for invalid email', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByLabel('Email').fill('invalid-email');
    await page.getByRole('button', { name: /send reset link/i }).click();
    await expect(page.getByRole('alert').filter({ hasText: /invalid email address/i })).toBeVisible({ timeout: 10000 });
  });

  test('should show success message for a real registered email', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByLabel('Email').fill(TEST_USER.email);
    await page.getByRole('button', { name: /send reset link/i }).click();

    // Real backend returns 200 — success state shows "Check your email"
    await expect(page.getByRole('heading', { name: /check your email/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/we've sent a password reset link/i)).toBeVisible();
  });

  test('should show success message even for non-existent email (no user enumeration)', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByLabel('Email').fill('does-not-exist@example.com');
    await page.getByRole('button', { name: /send reset link/i }).click();

    // Real backend returns 200 with the same message to avoid enumeration
    await expect(page.getByRole('heading', { name: /check your email/i })).toBeVisible({ timeout: 15000 });
  });

  test('should show debug token link in development for a real user', async ({ page, request }) => {
    // The backend only returns debug_token when DEBUG=true — detect and skip.
    const probe = await request.post(`${API_BASE}/api/v1/auth/forgot-password`, {
      data: { email: TEST_USER.email },
    });
    const body = await probe.json().catch(() => ({}));
    test.skip(!body.debug_token, 'backend not in DEBUG mode — no debug_token issued');

    await page.goto('/forgot-password');
    await page.getByLabel('Email').fill(TEST_USER.email);
    await page.getByRole('button', { name: /send reset link/i }).click();

    await expect(page.getByText(/development mode - debug token/i)).toBeVisible({ timeout: 15000 });
  });
});
