import { test, expect } from './helpers/auth';

test.describe('Brand sub-pages — real backend', () => {
  test('brand identity page renders heading or create-brand prompt', async ({ authenticatedPage: page }) => {
    await page.goto('/brand/identity');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/brand\/identity/);
    // User may or may not have a brand — accept either the heading or the "create a brand first" prompt
    await expect(
      page.getByRole('heading', { name: /brand identity/i }).or(page.getByText(/create a brand first/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('brand voice page renders heading or create-brand prompt', async ({ authenticatedPage: page }) => {
    await page.goto('/brand/voice');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/brand\/voice/);
    await expect(
      page.getByRole('heading', { name: /voice & tone/i }).or(page.getByText(/create a brand first/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('brand visual page renders heading or create-brand prompt', async ({ authenticatedPage: page }) => {
    await page.goto('/brand/visual');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/brand\/visual/);
    await expect(
      page.getByRole('heading', { name: /visual identity/i }).or(page.getByText(/create a brand first/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('brand guidelines page renders heading or create-brand prompt', async ({ authenticatedPage: page }) => {
    await page.goto('/brand/guidelines');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/brand\/guidelines/);
    await expect(
      page.getByRole('heading', { name: /brand guidelines/i }).or(page.getByText(/create a brand first/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('brand assets page renders heading or create-brand prompt', async ({ authenticatedPage: page }) => {
    await page.goto('/brand/assets');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/brand\/assets/);
    await expect(
      page.getByRole('heading', { name: /brand assets/i }).or(page.getByText(/create a brand first/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('brand health page renders heading or create-brand prompt', async ({ authenticatedPage: page }) => {
    await page.goto('/brand/health');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/brand\/health/);
    await expect(
      page.getByRole('heading', { name: /brand health/i }).or(page.getByText(/create a brand first/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('brand onboarding wizard renders heading', async ({ authenticatedPage: page }) => {
    await page.goto('/brand/onboarding');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/brand\/onboarding/);
    await expect(page.getByRole('heading', { name: /brand kit wizard/i })).toBeVisible({ timeout: 15000 });
  });

  test('brand monitoring page renders heading or create-brand prompt', async ({ authenticatedPage: page }) => {
    await page.goto('/brand/monitoring');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/brand\/monitoring/);
    await expect(
      page.getByRole('heading', { name: /brand monitoring/i }).or(page.getByText(/create a brand first/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('brand competitors page renders heading or create-brand prompt', async ({ authenticatedPage: page }) => {
    await page.goto('/brand/competitors');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/brand\/competitors/);
    await expect(
      page.getByRole('heading', { name: /competitor tracking/i }).or(page.getByText(/create a brand first/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('brand autopilot page renders heading or create-brand prompt', async ({ authenticatedPage: page }) => {
    await page.goto('/brand/autopilot');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/brand\/autopilot/);
    await expect(
      page.getByRole('heading', { name: /brand autopilot/i }).or(page.getByText(/create a brand first/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('shared brand guidelines page handles invalid token gracefully', async ({ page }) => {
    await page.goto('/brand/guidelines/share/invalid-token-xyz');
    await page.waitForLoadState('networkidle');
    // Should show "not found" message, not crash
    await expect(page.getByText(/not found|expired|loading/i)).toBeVisible({ timeout: 15000 });
  });
});
