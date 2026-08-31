import { test, expect } from '@playwright/test';

const ADMIN_EMAIL = 'tbaltzakis@cloudless.gr';
const ADMIN_PASSWORD = 'TH!123789th!';

test.describe('Admin Login — real credentials', () => {
  test('should log in as admin and reach dashboard', async ({ page }) => {
    await page.goto('http://localhost:8082/login');
    await expect(page).toHaveURL('/login');

    await page.getByLabel('Email').fill(ADMIN_EMAIL);
    await page.getByLabel('Password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: /sign in/i }).click();

    // Should navigate to dashboard
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
    await expect(page.getByRole('heading', { name: /good (morning|afternoon|evening)/i })).toBeVisible({ timeout: 15000 });
  });
});
