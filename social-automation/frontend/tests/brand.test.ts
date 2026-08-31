import { test, expect } from './helpers/auth';

test.describe('Brand Page — real backend', () => {
  test('should render the Create Your Brand Identity heading for a user without a brand', async ({ authenticatedPage: page }) => {
    await page.goto('/brand');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL('/brand');
    await expect(page.getByRole('heading', { name: /create your brand identity/i })).toBeVisible({ timeout: 15000 });
  });

  test('should show the brand DNA description', async ({ authenticatedPage: page }) => {
    await page.goto('/brand');
    await page.waitForLoadState('networkidle');
    await expect(page.getByText(/define your brand dna/i)).toBeVisible({ timeout: 20000 });
  });

  test('should show a Create Brand button', async ({ authenticatedPage: page }) => {
    await page.goto('/brand');
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('button', { name: /create brand/i })).toBeVisible({ timeout: 20000 });
  });

  test('should show the Website URL field', async ({ authenticatedPage: page }) => {
    await page.goto('/brand');
    await page.waitForLoadState('networkidle');
    // The form is revealed after clicking Create Brand Manually
    const btn = page.getByRole('button', { name: /create brand manually/i });
    await expect(btn).toBeVisible({ timeout: 20000 });
    await btn.click();
    await expect(page.getByLabel('Website URL')).toBeVisible({ timeout: 15000 });
  });
});
