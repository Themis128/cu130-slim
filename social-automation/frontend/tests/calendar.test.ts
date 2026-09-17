import { test, expect, API_BASE, setAuthCookies, registerAndLoginUser, type AuthTokens } from './helpers/auth';
import { randomUUID } from 'crypto';

/**
 * Calendar Page — real backend.
 *
 * The calendar hides the month grid behind an empty-state card when the
 * viewed month has zero posts, so fresh-user tests assert the empty state,
 * and grid-specific tests seed a scheduled post for a dedicated user.
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

  test('should show empty state for a fresh user', async ({ authenticatedPage: page }) => {
    await page.goto('/calendar');
    await page.waitForLoadState('networkidle');
    // With zero posts the grid is replaced by the empty-state card
    await expect(page.getByRole('heading', { name: /nothing scheduled yet/i })).toBeVisible({ timeout: 20000 });
    await expect(page.getByRole('link', { name: /schedule your first post/i })).toBeVisible();
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
    await page.getByRole('button', { name: 'Next month' }).click();
    await page.waitForTimeout(300);

    await page.getByRole('button', { name: /\w+ \d{4}/ }).click();
    const currentMonth = new Date().toLocaleString('default', { month: 'long', year: 'numeric' });
    await expect(page.getByRole('button', { name: new RegExp(currentMonth, 'i') })).toBeVisible();
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
});

/**
 * Grid interactions need at least one post — the month grid only renders
 * when filteredPosts.length > 0. Uses a dedicated user so the shared
 * TEST_USER stays post-free for the empty-state tests above.
 */
test.describe('Calendar grid — seeded post', () => {
  let tokens: AuthTokens;

  test.beforeAll(async ({ request }) => {
    const email = `cal-e2e-${randomUUID().slice(0, 8)}@example.com`;
    tokens = await registerAndLoginUser(email, 'Cal-E2E-Pass-123!', 'Calendar E2E');

    // Seed a post scheduled later this month (clamped to day 28 so it always
    // stays inside the current month view).
    const when = new Date();
    when.setDate(Math.min(when.getDate() + 2, 28));
    when.setHours(12, 0, 0, 0);
    const res = await request.post(`${API_BASE}/api/v1/content/posts`, {
      headers: { Authorization: `Bearer ${tokens.access_token}` },
      data: { content_text: 'Calendar E2E seeded post', scheduled_at: when.toISOString() },
    });
    expect(res.ok(), await res.text()).toBeTruthy();
  });

  test.beforeEach(async ({ page }) => {
    await setAuthCookies(page, tokens);
  });

  test('should render the month grid with day cells and weekday headers', async ({ page }) => {
    await page.goto('/calendar');
    await page.waitForLoadState('networkidle');
    for (const day of ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']) {
      await expect(page.getByText(day, { exact: true }).first()).toBeVisible({ timeout: 15000 });
    }
    const dayCells = page.locator('.min-h-\\[130px\\]');
    await expect(dayCells.first()).toBeVisible();
    expect(await dayCells.count()).toBeGreaterThanOrEqual(28);
  });

  test('should highlight today', async ({ page }) => {
    await page.goto('/calendar');
    const today = new Date().getDate();
    await expect(page.locator('.bg-primary').getByText(today.toString()).first()).toBeVisible({ timeout: 15000 });
  });

  test('should allow selecting a day and show the day detail panel', async ({ page }) => {
    await page.goto('/calendar');
    await page.waitForLoadState('networkidle');
    const dayCell = page.locator('.min-h-\\[130px\\].cursor-pointer').first();
    await dayCell.click();
    await expect(page.getByRole('link', { name: /schedule post/i })).toBeVisible();
  });

  test('should switch to week view', async ({ page }) => {
    await page.goto('/calendar');
    await page.getByRole('button', { name: /week/i }).click();
    await page.waitForTimeout(300);
    await expect(page.locator('.rounded-xl.border').first()).toBeVisible();
  });
});
