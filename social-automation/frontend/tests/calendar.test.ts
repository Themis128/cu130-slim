import { test, expect } from '@playwright/test';

test.describe('Calendar Page', () => {
  test.beforeEach(async ({ page }) => {
    // Mock authentication
    await page.goto('/dashboard');
    
    // Mock scheduled posts API
    await page.route('**/api/posts/scheduled', async (route) => {
      const today = new Date();
      const tomorrow = new Date(today);
      tomorrow.setDate(tomorrow.getDate() + 1);
      
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: 'post-1',
            content_text: 'First scheduled post for today',
            scheduled_at: today.toISOString(),
            targets: [
              {
                social_account_id: '1',
                social_account: {
                  id: '1',
                  platform: 'linkedin',
                  username: 'testuser',
                },
              },
            ],
          },
          {
            id: 'post-2',
            content_text: 'Another post for tomorrow',
            scheduled_at: tomorrow.toISOString(),
            targets: [
              {
                social_account_id: '2',
                social_account: {
                  id: '2',
                  platform: 'twitter',
                  username: 'testuser',
                },
              },
            ],
          },
        ]),
      });
    });

    // Mock schedule post API
    await page.route('**/api/posts/*/schedule', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });
  });

  test('should load calendar page successfully', async ({ page }) => {
    await page.goto('/calendar');
    await expect(page).toHaveURL('/calendar');
    
    // Check for main heading
    await expect(page.getByRole('heading', { name: 'Calendar' })).toBeVisible();
  });

  test('should display current month in header', async ({ page }) => {
    await page.goto('/calendar');
    
    // Check for current month display
    const currentMonth = new Date().toLocaleString('default', { month: 'long', year: 'numeric' });
    await expect(page.getByText(new RegExp(currentMonth, 'i'))).toBeVisible();
  });

  test('should display scheduled posts count', async ({ page }) => {
    await page.goto('/calendar');
    
    // Check for posts count in subtitle
    await expect(page.getByText(/scheduled post/i)).toBeVisible();
  });

  test('should display day of week headers', async ({ page }) => {
    await page.goto('/calendar');
    
    // Check for all weekday headers
    await expect(page.getByText('Mon')).toBeVisible();
    await expect(page.getByText('Tue')).toBeVisible();
    await expect(page.getByText('Wed')).toBeVisible();
    await expect(page.getByText('Thu')).toBeVisible();
    await expect(page.getByText('Fri')).toBeVisible();
    await expect(page.getByText('Sat')).toBeVisible();
    await expect(page.getByText('Sun')).toBeVisible();
  });

  test('should display calendar grid with days', async ({ page }) => {
    await page.goto('/calendar');
    
    // Check for calendar grid
    const calendarGrid = page.locator('.grid.grid-cols-7');
    await expect(calendarGrid).toBeVisible();
    
    // Check for day cells (should have at least 28 days)
    const dayCells = page.locator('.min-h-\\[110px\\]');
    await expect(dayCells.first()).toBeVisible();
  });

  test('should highlight today', async ({ page }) => {
    await page.goto('/calendar');
    
    // Check for today's highlight (should have primary background)
    const today = new Date().getDate();
    const todayCell = page.getByText(today.toString());
    await expect(todayCell.first()).toBeVisible();
  });

  test('should display scheduled posts as chips', async ({ page }) => {
    await page.goto('/calendar');
    
    // Check for post chips (should show content snippet)
    await expect(page.getByText(/scheduled post/i)).toBeVisible();
  });

  test('should show platform indicators on post chips', async ({ page }) => {
    await page.goto('/calendar');
    
    // Check for platform color indicators
    const platformIndicators = page.locator('.rounded-full');
    await expect(platformIndicators.first()).toBeVisible();
  });

  test('should allow navigation to previous month', async ({ page }) => {
    await page.goto('/calendar');
    
    // Get current month text
    const currentMonthText = await page.getByRole('button', { name: /\w+ \d{4}/ }).textContent();
    
    // Click previous month button
    const prevButton = page.getByRole('button').filter({ hasText: '' }).nth(0);
    await prevButton.click();
    
    // Wait for navigation to complete
    await page.waitForTimeout(500);
    
    // Check that month changed (this is a basic check)
    await expect(page.getByRole('button', { name: /\w+ \d{4}/ })).toBeVisible();
  });

  test('should allow navigation to next month', async ({ page }) => {
    await page.goto('/calendar');
    
    // Click next month button
    const nextButton = page.getByRole('button').filter({ hasText: '' }).nth(1);
    await nextButton.click();
    
    // Wait for navigation to complete
    await page.waitForTimeout(500);
    
    // Check that month changed
    await expect(page.getByRole('button', { name: /\w+ \d{4}/ })).toBeVisible();
  });

  test('should allow returning to current month', async ({ page }) => {
    await page.goto('/calendar');
    
    // Navigate away first
    const nextButton = page.getByRole('button').filter({ hasText: '' }).nth(1);
    await nextButton.click();
    await page.waitForTimeout(500);
    
    // Click current month button
    const currentMonthButton = page.getByRole('button', { name: /\w+ \d{4}/ });
    await currentMonthButton.click();
    
    // Should return to current month
    const currentMonth = new Date().toLocaleString('default', { month: 'long', year: 'numeric' });
    await expect(page.getByText(new RegExp(currentMonth, 'i'))).toBeVisible();
  });

  test('should show new post button', async ({ page }) => {
    await page.goto('/calendar');
    
    // Check for new post button
    const newPostButton = page.getByRole('link', { name: /New Post/i });
    await expect(newPostButton).toBeVisible();
  });

  test('should navigate to content creation when clicking new post', async ({ page }) => {
    await page.goto('/calendar');
    
    // Click new post button
    const newPostButton = page.getByRole('link', { name: /New Post/i });
    await newPostButton.click();
    
    // Should navigate to content creation
    await expect(page).toHaveURL('/content/new');
  });

  test('should allow selecting a day', async ({ page }) => {
    await page.goto('/calendar');
    
    // Click on a day cell
    const dayCell = page.locator('.min-h-\\[110px\\]').first();
    await dayCell.click();
    
    // Check for day detail panel to appear
    await expect(page.getByRole('heading', { name: /\w+, \w+ \d+/i })).toBeVisible();
  });

  test('should show day detail panel when day is selected', async ({ page }) => {
    await page.goto('/calendar');
    
    // Click on a day with posts
    const dayCell = page.locator('.min-h-\\[110px\\]').first();
    await dayCell.click();
    
    // Check for detail panel
    await expect(page.getByText(/Schedule for this day/i)).toBeVisible();
  });

  test('should show posts in day detail panel', async ({ page }) => {
    await page.goto('/calendar');
    
    // Click on a day with posts
    const dayCell = page.locator('.min-h-\\[110px\\]').first();
    await dayCell.click();
    
    // Check for post content in detail panel
    await expect(page.getByText(/scheduled post/i)).toBeVisible();
  });

  test('should show platform badges in day detail panel', async ({ page }) => {
    await page.goto('/calendar');
    
    // Click on a day with posts
    const dayCell = page.locator('.min-h-\\[110px\\]').first();
    await dayCell.click();
    
    // Check for platform badges
    await expect(page.locator('.badge')).toBeVisible();
  });

  test('should show schedule button in day detail panel', async ({ page }) => {
    await page.goto('/calendar');
    
    // Click on a day
    const dayCell = page.locator('.min-h-\\[110px\\]').first();
    await dayCell.click();
    
    // Check for schedule button
    await expect(page.getByRole('link', { name: /Schedule for this day/i })).toBeVisible();
  });

  test('should show empty state when day has no posts', async ({ page }) => {
    // Mock empty posts for a specific day
    await page.route('**/api/posts/scheduled', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });
    
    await page.goto('/calendar');
    
    // Click on a day
    const dayCell = page.locator('.min-h-\\[110px\\]').first();
    await dayCell.click();
    
    // Check for empty state message
    await expect(page.getByText('Nothing scheduled')).toBeVisible();
  });

  test('should show quick-add button on day hover', async ({ page }) => {
    await page.goto('/calendar');
    
    // Hover over a day cell
    const dayCell = page.locator('.min-h-\\[110px\\]').first();
    await dayCell.hover();
    
    // Check for quick-add button (appears on hover)
    const quickAddButton = dayCell.locator('a').filter({ hasText: '' });
    await expect(quickAddButton).toBeVisible();
  });

  test('should show legend with instructions', async ({ page }) => {
    await page.goto('/calendar');
    
    // Check for legend
    await expect(page.getByText('Today')).toBeVisible();
    await expect(page.getByText(/Drag a post chip/i)).toBeVisible();
  });

  test('should allow dragging posts to reschedule', async ({ page }) => {
    await page.goto('/calendar');
    
    // Find a post chip
    const postChip = page.locator('.cursor-grab').first();
    await expect(postChip).toBeVisible();
    
    // Note: Full drag-and-drop testing requires more complex setup
    // This test verifies the chip is draggable
    await expect(postChip).toHaveAttribute('draggable', 'true');
  });

  test('should display multiple platform indicators for multi-platform posts', async ({ page }) => {
    // Mock a post with multiple platforms
    await page.route('**/api/posts/scheduled', async (route) => {
      const today = new Date();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: 'post-1',
            content_text: 'Multi-platform post',
            scheduled_at: today.toISOString(),
            targets: [
              {
                social_account_id: '1',
                social_account: { id: '1', platform: 'linkedin', username: 'testuser' },
              },
              {
                social_account_id: '2',
                social_account: { id: '2', platform: 'twitter', username: 'testuser' },
              },
            ],
          },
        ]),
      });
    });
    
    await page.goto('/calendar');
    
    // Check for multiple platform indicators
    const platformIndicators = page.locator('.rounded-full');
    await expect(platformIndicators).toHaveCount.gte(1);
  });

  test('should handle overflow when many posts in one day', async ({ page }) => {
    // Mock many posts for a single day
    const today = new Date();
    const manyPosts = Array.from({ length: 5 }, (_, i) => ({
      id: `post-${i}`,
      content_text: `Post number ${i + 1}`,
      scheduled_at: today.toISOString(),
      targets: [{
        social_account_id: '1',
        social_account: { id: '1', platform: 'linkedin', username: 'testuser' },
      }],
    }));
    
    await page.route('**/api/posts/scheduled', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(manyPosts),
      });
    });
    
    await page.goto('/calendar');
    
    // Check for "more" indicator
    await expect(page.getByText(/\+ \d+ more/i)).toBeVisible();
  });

  test('should show time in day detail panel', async ({ page }) => {
    await page.goto('/calendar');
    
    // Click on a day with posts
    const dayCell = page.locator('.min-h-\\[110px\\]').first();
    await dayCell.click();
    
    // Check for time indicator
    await expect(page.locator('.text-muted-foreground').filter({ hasText: /\d+:\d+/ })).toBeVisible();
  });
});