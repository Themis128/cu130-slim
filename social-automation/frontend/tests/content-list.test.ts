import { test, expect } from './helpers/auth';

test.describe('Content redirect — real backend', () => {
  test('should redirect /content to /content/new', async ({ authenticatedPage: page }) => {
    await page.goto('/content');
    await expect(page).toHaveURL(/\/content\/new/, { timeout: 15000 });
  });

  test('should load the content creation page with New Post heading', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByRole('heading', { name: 'New Post' })).toBeVisible({ timeout: 15000 });
  });

  test('should show the content editor textarea', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByPlaceholder('What do you want to share?')).toBeVisible({ timeout: 15000 });
  });

  test('should display content type buttons', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByRole('button', { name: 'Post' }).first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: 'Carousel' })).toBeVisible();
  });
});

test.describe('Dashboard content integration — real backend', () => {
  test('should show stat cards with Published, Scheduled, Drafts, and Failed labels', async ({ authenticatedPage: page }) => {
    await page.goto('/dashboard');
    // Use exact text matching for stat card labels
    await expect(page.getByText('Published').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Scheduled').first()).toBeVisible();
    await expect(page.getByText('Drafts').first()).toBeVisible();
    await expect(page.getByText('Failed').first()).toBeVisible();
  });

  test('should show Top Performing Posts section', async ({ authenticatedPage: page }) => {
    await page.goto('/dashboard');
    await expect(page.getByText(/top performing posts/i)).toBeVisible({ timeout: 15000 });
  });

  test('should show empty state for top posts on a fresh account', async ({ authenticatedPage: page }) => {
    await page.goto('/dashboard');
    await expect(page.getByText(/no published posts yet/i)).toBeVisible({ timeout: 15000 });
  });
});

test.describe('Calendar scheduled-content integration — real backend', () => {
  test('should show zero posts for a fresh user', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    await expect(page.getByText(/0 posts in/i)).toBeVisible({ timeout: 15000 });
  });

  test('should show empty state in day detail panel', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    // Click the first day cell to open the detail panel
    const dayCells = page.locator('.min-h-\\[130px\\].cursor-pointer');
    await dayCells.first().click();
    // The day detail shows "Nothing scheduled" for a fresh user
    await expect(page.getByText(/nothing scheduled/i).first()).toBeVisible({ timeout: 15000 });
  });
});
