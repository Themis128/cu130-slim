import { test, expect } from './helpers/auth';

/**
 * Calendar Page — real backend.
 *
 * A fresh test user has zero scheduled posts, so the calendar renders
 * with an empty grid. We verify the structural elements that always render
 * regardless of data: header, month label, weekday headers, grid, legend,
 * and the New Post button.
 */

test.describe('Calendar Page — real backend', () => {
  test('should load and show the current month', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    await expect(page).toHaveURL('/calendar');

    // Main heading
    await expect(page.getByRole('heading', { name: 'Calendar' })).toBeVisible();

    // Current month label (e.g. "August 2026") — rendered as a button
    const currentMonth = new Date().toLocaleString('default', { month: 'long', year: 'numeric' });
    await expect(page.getByRole('button', { name: new RegExp(currentMonth, 'i') })).toBeVisible();
  });

  test('should display weekday headers', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    for (const day of ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']) {
      await expect(page.getByText(day).first()).toBeVisible();
    }
  });

  test('should display the calendar grid with day cells', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    // The grid uses min-h-[130px] cells
    const dayCells = page.locator('.min-h-\\[130px\\]');
    await expect(dayCells.first()).toBeVisible();
    // At least 28 day cells in any month
    const count = await dayCells.count();
    expect(count).toBeGreaterThanOrEqual(28);
  });

  test('should highlight today', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    const today = new Date().getDate();
    // Today's cell has a primary-colored circle around the day number
    const todayNumber = page.locator('.bg-primary').getByText(today.toString());
    await expect(todayNumber.first()).toBeVisible();
  });

  test('should show zero posts for a fresh user', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    await page.waitForLoadState('networkidle');
    // The subtitle shows "0 posts in <month>"
    await expect(page.getByText(/0 posts in/i)).toBeVisible({ timeout: 20000 });
  });

  test('should show the New Post button', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    const newPostLink = page.getByRole('link', { name: /new post/i });
    await expect(newPostLink).toBeVisible();
  });

  test('should navigate to content creation when clicking New Post', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    await page.waitForLoadState('networkidle');
    await page.getByRole('link', { name: /new post/i }).click();
    await expect(page).toHaveURL(/\/content\/new/, { timeout: 20000 });
  });

  test('should allow navigation to the previous month', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    const monthButton = page.getByRole('button', { name: /\w+ \d{4}/ });
    const initialMonth = await monthButton.textContent();

    // Click the previous-month button (has aria-label="Previous month")
    await page.getByRole('button', { name: 'Previous month' }).click();
    await page.waitForTimeout(300);

    const newMonth = await monthButton.textContent();
    expect(newMonth).not.toBe(initialMonth);
  });

  test('should allow navigation to the next month', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    const monthButton = page.getByRole('button', { name: /\w+ \d{4}/ });
    const initialMonth = await monthButton.textContent();

    await page.getByRole('button', { name: 'Next month' }).click();
    await page.waitForTimeout(300);

    const newMonth = await monthButton.textContent();
    expect(newMonth).not.toBe(initialMonth);
  });

  test('should return to current month when clicking the month button', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    // Navigate away
    await page.getByRole('button', { name: 'Next month' }).click();
    await page.waitForTimeout(300);

    // Click the month button to return to today
    await page.getByRole('button', { name: /\w+ \d{4}/ }).click();
    const currentMonth = new Date().toLocaleString('default', { month: 'long', year: 'numeric' });
    await expect(page.getByRole('button', { name: new RegExp(currentMonth, 'i') })).toBeVisible();
  });

  test('should allow selecting a day and show the day detail panel', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    // Click the first actual day cell (padding cells lack cursor-pointer)
    const dayCell = page.locator('.min-h-\\[130px\\].cursor-pointer').first();
    await dayCell.click();

    // Day detail panel appears with a "Schedule post" link
    await expect(page.getByRole('link', { name: /schedule post/i })).toBeVisible();
  });

  test('should show empty state in day detail for a fresh user', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    const dayCell = page.locator('.min-h-\\[130px\\].cursor-pointer').first();
    await dayCell.click();
    await expect(page.getByText(/nothing scheduled/i).first()).toBeVisible();
  });

  test('should show the status legend', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    await expect(page.getByText(/status:/i)).toBeVisible();
    await expect(page.getByText(/drag a chip to reschedule/i)).toBeVisible();
  });

  test('should show platform filter buttons', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    await expect(page.getByRole('button', { name: /all platforms/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /linkedin/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /twitter/i })).toBeVisible();
  });

  test('should show month/week view toggle', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    await expect(page.getByRole('button', { name: /month/i }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /week/i })).toBeVisible();
  });

  test('should switch to week view', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    await page.getByRole('button', { name: /week/i }).click();
    await page.waitForTimeout(300);
    // Week view renders a WeekCalendar component inside a bordered container
    await expect(page.locator('.rounded-xl.border').first()).toBeVisible();
  });
});
