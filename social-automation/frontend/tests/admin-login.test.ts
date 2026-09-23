import { test, expect } from '@playwright/test';

const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL || 'tbaltzakis@cloudless.gr';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD;
if (!ADMIN_PASSWORD) {
  throw new Error('E2E_ADMIN_PASSWORD env var required — never hardcode credentials');
}

test.describe('Admin Login — real credentials', () => {
  test('should log in as admin and reach dashboard', async ({ page }) => {
    await page.goto('http://localhost:8082/login');
    await expect(page).toHaveURL('/login');

    await page.getByLabel('Email').fill(ADMIN_EMAIL);
    await page.getByLabel('Password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: /sign in/i }).click();

    // Should navigate to dashboard
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
    await expect(page.getByText(/good (morning|afternoon|evening)/i)).toBeVisible({ timeout: 15000 });
  });
});
