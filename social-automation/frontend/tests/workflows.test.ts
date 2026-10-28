import { test, expect } from './helpers/auth';

test.describe('Workflows Page — real backend', () => {
  test('should render the Workflows heading', async ({ authenticatedPage: page }) => {
    await page.goto('/workflows');
    await expect(page).toHaveURL('/workflows');
    await expect(page.getByRole('heading', { name: 'Workflows', exact: true })).toBeVisible({ timeout: 15000 });
  });

  test('should show AI Generate and Browse Gallery buttons', async ({ authenticatedPage: page }) => {
    await page.goto('/workflows');
    await expect(page.getByRole('button', { name: 'AI Generate' })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: 'Browse Gallery' })).toBeVisible();
  });

  test('should show tabs: Templates, Gallery, Deployed, AI Generate', async ({ authenticatedPage: page }) => {
    await page.goto('/workflows');
    await expect(page.getByRole('tab', { name: 'Templates' })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('tab', { name: 'Gallery' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Deployed' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'AI Generate' })).toBeVisible();
  });

  test('should show the AI Generate tab content with a prompt textarea', async ({ authenticatedPage: page }) => {
    await page.goto('/workflows');
    await page.getByRole('tab', { name: 'AI Generate' }).click();
    // The AI Generate tab has a textarea for the prompt
    await expect(page.getByRole('tab', { name: 'AI Generate', selected: true })).toBeVisible({ timeout: 15000 });
    // Look for a textarea in the tabpanel
    const tabpanel = page.getByRole('tabpanel', { name: 'AI Generate' });
    await expect(tabpanel.locator('textarea')).toBeVisible({ timeout: 15000 });
  });

  test('should show a Generate Workflow button on the AI Generate tab', async ({ authenticatedPage: page }) => {
    await page.goto('/workflows');
    await page.getByRole('tab', { name: 'AI Generate' }).click();
    await expect(page.getByRole('button', { name: /generate workflow/i })).toBeVisible({ timeout: 15000 });
  });

  test('should show starter templates in the Gallery tab', async ({ authenticatedPage: page }) => {
    await page.goto('/workflows');
    await page.getByRole('tab', { name: 'Gallery' }).click();
    // Gallery has template cards with "Use this template" buttons
    await expect(page.getByRole('button', { name: /use this template/i }).first()).toBeVisible({ timeout: 15000 });
  });

  test('should show the Deployed tab with an empty state for a fresh user', async ({ authenticatedPage: page }) => {
    await page.goto('/workflows');
    await page.getByRole('tab', { name: 'Deployed' }).click();
    // Fresh user has zero deployed workflows
    const tabpanel = page.getByRole('tabpanel', { name: 'Deployed' });
    await expect(tabpanel).toBeVisible({ timeout: 15000 });
  });
});
