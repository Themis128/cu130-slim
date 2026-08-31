import { test, expect } from './helpers/auth';

/**
 * Content Creation Page — real backend.
 *
 * A fresh test user has zero connected accounts, so all platform buttons
 * are disabled. We verify the page structure, validation, and that
 * content type buttons navigate to the correct routes.
 */

test.describe('Content Creation Page — real backend', () => {
  test('should load the content creation page', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page).toHaveURL('/content/new');
    await expect(page.getByRole('heading', { name: 'New Post' })).toBeVisible();
    await expect(page.getByText('Create and schedule content across platforms')).toBeVisible();
  });

  test('should display content type buttons', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByRole('button', { name: 'Post' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Carousel' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Thread' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Poll' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Story' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Article' })).toBeVisible();
  });

  test('should show Post as the active content type by default', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    const postButton = page.getByRole('button', { name: 'Post' });
    await expect(postButton).toHaveClass(/border-primary/);
  });

  test('should display the Platforms section', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByRole('heading', { name: 'Platforms' })).toBeVisible();
  });

  test('should show all 6 platform buttons (disabled for a fresh user)', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    // All platforms render as buttons, but disabled since no accounts are connected
    await expect(page.getByRole('button', { name: /LinkedIn/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Twitter/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Instagram/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Facebook/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Threads/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /TikTok/i })).toBeVisible();
  });

  test('should allow typing in the content editor', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    const textarea = page.getByPlaceholder('What do you want to share?');
    await expect(textarea).toBeVisible();
    await textarea.fill('This is a test post for social media');
    await expect(textarea).toHaveValue('This is a test post for social media');
  });

  test('should display tone selection options', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByRole('button', { name: /Professional/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Casual/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Witty/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Inspirational/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Educational/i })).toBeVisible();
  });

  test('should allow tone selection', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    const wittyButton = page.getByRole('button', { name: /Witty/i });
    await wittyButton.click();
    await expect(wittyButton).toHaveClass(/border-primary/);
  });

  test('should show Save Draft button', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByRole('button', { name: /Save Draft/i })).toBeVisible();
  });

  test('should show Schedule button', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByRole('button', { name: /Schedule/i })).toBeVisible();
  });

  test('should show Publish Now button', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByRole('button', { name: /Publish Now/i })).toBeVisible();
  });

  test('should show Live Preview section', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByRole('heading', { name: 'Live Preview' })).toBeVisible();
  });

  test('should show preview placeholder when no platform is selected', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByText(/select a platform to see a live preview/i)).toBeVisible();
  });

  test('should show Generate button', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await expect(page.getByRole('button', { name: /Generate/i })).toBeVisible();
  });

  test('should navigate to Carousel creation page', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await page.getByRole('button', { name: 'Carousel' }).click();
    await expect(page).toHaveURL('/content/carousel/new', { timeout: 15000 });
  });

  test('should navigate to Thread creation page', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await page.getByRole('button', { name: 'Thread' }).click();
    await expect(page).toHaveURL('/content/thread/new', { timeout: 15000 });
  });

  test('should navigate to Article creation page', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    await page.getByRole('button', { name: 'Article' }).click();
    await expect(page).toHaveURL('/content/article/new', { timeout: 15000 });
  });

  test('should show a link to accounts page when no platforms are connected', async ({ authenticatedPage: page }) => {
    await page.goto('/content/new');
    // The page shows a help text linking to accounts when no platforms are connected
    await expect(page.getByText(/connect.*account|no.*account.*connect/i)).toBeVisible({ timeout: 10000 });
  });
});
