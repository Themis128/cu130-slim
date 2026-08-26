import { test, expect } from '@playwright/test';

test.describe('Content Creation Page', () => {
  test.beforeEach(async ({ page }) => {
    // Mock authentication
    await page.goto('/dashboard');
    
    // Mock connected accounts
    await page.route('**/api/accounts', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: '1',
            platform: 'linkedin',
            username: 'testuser',
            connected_at: new Date().toISOString(),
          },
          {
            id: '2',
            platform: 'twitter',
            username: 'testuser',
            connected_at: new Date().toISOString(),
          },
        ]),
      });
    });

    // Mock create post API
    await page.route('**/api/posts', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: { id: 'post-123' },
        }),
      });
    });

    // Mock publish API
    await page.route('**/api/posts/*/publish', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    // Mock media upload API
    await page.route('**/api/media', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'media-123',
          url: 'https://example.com/image.jpg',
        }),
      });
    });

    // Mock AI content generation
    await page.route('**/api/ai/generate', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            content: 'This is AI-generated content about our latest product launch #innovation #tech',
            hashtags: ['innovation', 'tech'],
          },
        }),
      });
    });
  });

  test('should load content creation page successfully', async ({ page }) => {
    await page.goto('/content/new');
    await expect(page).toHaveURL('/content/new');
    
    // Check for main heading
    await expect(page.getByRole('heading', { name: 'New Post' })).toBeVisible();
    await expect(page.getByText('Create and schedule content across platforms')).toBeVisible();
  });

  test('should display content type options', async ({ page }) => {
    await page.goto('/content/new');
    
    // Check for all content type buttons
    await expect(page.getByRole('button', { name: 'Post' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Carousel' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Thread' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Poll' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Story' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Article' })).toBeVisible();
    
    // Check that Post is selected by default
    const postButton = page.getByRole('button', { name: 'Post' });
    await expect(postButton).toHaveClass(/border-primary/);
  });

  test('should display platform selector with connected accounts', async ({ page }) => {
    await page.goto('/content/new');
    
    // Check for platform selector heading
    await expect(page.getByRole('heading', { name: 'Platforms' })).toBeVisible();
    
    // Check for connected platforms
    await expect(page.getByText('LinkedIn')).toBeVisible();
    await expect(page.getByText('Twitter / X')).toBeVisible();
    
    // Check for unconnected platforms (disabled)
    await expect(page.getByText('Instagram')).toBeVisible();
    await expect(page.getByText('Facebook')).toBeVisible();
    await expect(page.getByText('Threads')).toBeVisible();
  });

  test('should allow platform selection', async ({ page }) => {
    await page.goto('/content/new');
    
    // Select LinkedIn
    const linkedinButton = page.getByRole('button', { name: /LinkedIn/i }).first();
    await linkedinButton.click();
    
    // Verify it's selected
    await expect(linkedinButton).toHaveClass(/border-primary/);
    
    // Select Twitter as well
    const twitterButton = page.getByRole('button', { name: /Twitter/i }).first();
    await twitterButton.click();
    
    // Verify both are selected
    await expect(twitterButton).toHaveClass(/border-primary/);
  });

  test('should show error when selecting unconnected platform', async ({ page }) => {
    await page.goto('/content/new');
    
    // Try to select Instagram (unconnected)
    const instagramButton = page.getByRole('button', { name: /Instagram/i }).first();
    await instagramButton.click();
    
    // Check for error toast
    await expect(page.getByText(/Connect your instagram account first/i)).toBeVisible();
  });

  test('should allow content typing in editor', async ({ page }) => {
    await page.goto('/content/new');
    
    // Find the content textarea
    const textarea = page.getByPlaceholder('What do you want to share?');
    await expect(textarea).toBeVisible();
    
    // Type content
    await textarea.fill('This is a test post for social media');
    
    // Verify content is entered
    await expect(textarea).toHaveValue('This is a test post for social media');
  });

  test('should display character count', async ({ page }) => {
    await page.goto('/content/new');
    
    // Type content
    const textarea = page.getByPlaceholder('What do you want to share?');
    await textarea.fill('Test content');
    
    // Check for character count
    await expect(page.getByText('12 chars')).toBeVisible();
  });

  test('should show platform-specific character limits', async ({ page }) => {
    await page.goto('/content/new');
    
    // Select LinkedIn first
    await page.getByRole('button', { name: /LinkedIn/i }).first().click();
    
    // Type content
    const textarea = page.getByPlaceholder('What do you want to share?');
    await textarea.fill('Test content');
    
    // Check for LinkedIn character count indicator
    await expect(page.getByText(/LinkedIn/i)).toBeVisible();
    await expect(page.getByText(/remaining/i)).toBeVisible();
  });

  test('should display tone selection options', async ({ page }) => {
    await page.goto('/content/new');
    
    // Check for all tone options
    await expect(page.getByRole('button', { name: /Professional/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Casual/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Witty/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Inspirational/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Educational/i })).toBeVisible();
  });

  test('should allow tone selection', async ({ page }) => {
    await page.goto('/content/new');
    
    // Select Witty tone
    const wittyButton = page.getByRole('button', { name: /Witty/i });
    await wittyButton.click();
    
    // Verify it's selected
    await expect(wittyButton).toHaveClass(/border-primary/);
  });

  test('should generate AI content', async ({ page }) => {
    await page.goto('/content/new');
    
    // Select a platform first
    await page.getByRole('button', { name: /LinkedIn/i }).first().click();
    
    // Click generate button
    const generateButton = page.getByRole('button', { name: /Generate/i });
    await generateButton.click();
    
    // Wait for AI generation to complete
    await expect(page.getByText('AI content generated')).toBeVisible();
    
    // Check that content was generated
    const textarea = page.getByPlaceholder('What do you want to share?');
    await expect(textarea).toHaveValue(/AI-generated content/i);
  });

  test('should show live preview when platform is selected', async ({ page }) => {
    await page.goto('/content/new');
    
    // Select LinkedIn
    await page.getByRole('button', { name: /LinkedIn/i }).first().click();
    
    // Type content
    const textarea = page.getByPlaceholder('What do you want to share?');
    await textarea.fill('Test post content');
    
    // Check for live preview section
    await expect(page.getByRole('heading', { name: 'Live Preview' })).toBeVisible();
    
    // Check for platform name in preview
    await expect(page.getByText('LinkedIn')).toBeVisible();
  });

  test('should allow switching between platform previews', async ({ page }) => {
    await page.goto('/content/new');
    
    // Select both LinkedIn and Twitter
    await page.getByRole('button', { name: /LinkedIn/i }).first().click();
    await page.getByRole('button', { name: /Twitter/i }).first().click();
    
    // Type content
    const textarea = page.getByPlaceholder('What do you want to share?');
    await textarea.fill('Test post content');
    
    // Check for platform switcher buttons in preview
    const platformSwitchers = page.locator('.lg\\:sticky button').filter({ hasText: /^[in𝕏@]$/ });
    await expect(platformSwitchers).toHaveCount(2);
    
    // Click Twitter switcher
    await platformSwitchers.nth(1).click();
    
    // Verify preview updates (Twitter preview should be different)
    await expect(page.getByText('Twitter / X')).toBeVisible();
  });

  test('should show save draft button', async ({ page }) => {
    await page.goto('/content/new');
    
    // Check for save draft button
    const saveDraftButton = page.getByRole('button', { name: /Save Draft/i });
    await expect(saveDraftButton).toBeVisible();
  });

  test('should show schedule button', async ({ page }) => {
    await page.goto('/content/new');
    
    // Check for schedule button
    const scheduleButton = page.getByRole('button', { name: /Schedule/i });
    await expect(scheduleButton).toBeVisible();
  });

  test('should show publish now button', async ({ page }) => {
    await page.goto('/content/new');
    
    // Check for publish now button
    const publishButton = page.getByRole('button', { name: /Publish Now/i });
    await expect(publishButton).toBeVisible();
  });

  test('should open schedule dialog', async ({ page }) => {
    await page.goto('/content/new');
    
    // Click schedule button
    const scheduleButton = page.getByRole('button', { name: /Schedule/i });
    await scheduleButton.click();
    
    // Check for dialog
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Schedule Post' })).toBeVisible();
    
    // Check for datetime input
    await expect(page.getByRole('textbox')).toBeVisible();
  });

  test('should validate content before publishing', async ({ page }) => {
    await page.goto('/content/new');
    
    // Try to publish without content
    const publishButton = page.getByRole('button', { name: /Publish Now/i });
    await publishButton.click();
    
    // Check for validation error
    await expect(page.getByText('Please add some content')).toBeVisible();
  });

  test('should validate platform selection before publishing', async ({ page }) => {
    await page.goto('/content/new');
    
    // Add content but don't select platform
    const textarea = page.getByPlaceholder('What do you want to share?');
    await textarea.fill('Test content');
    
    // Try to publish
    const publishButton = page.getByRole('button', { name: /Publish Now/i });
    await publishButton.click();
    
    // Check for validation error
    await expect(page.getByText('Select at least one platform')).toBeVisible();
  });

  test('should successfully publish post', async ({ page }) => {
    await page.goto('/content/new');
    
    // Select platform and add content
    await page.getByRole('button', { name: /LinkedIn/i }).first().click();
    const textarea = page.getByPlaceholder('What do you want to share?');
    await textarea.fill('Test content to publish');
    
    // Publish
    const publishButton = page.getByRole('button', { name: /Publish Now/i });
    await publishButton.click();
    
    // Check for success message
    await expect(page.getByText('Post published')).toBeVisible();
  });

  test('should navigate to different content types', async ({ page }) => {
    await page.goto('/content/new');
    
    // Click on Carousel
    const carouselButton = page.getByRole('button', { name: 'Carousel' });
    await carouselButton.click();
    
    // Should navigate to carousel creation page
    await expect(page).toHaveURL('/content/carousel/new');
  });

  test('should show link to accounts page when no platforms selected', async ({ page }) => {
    await page.goto('/content/new');
    
    // Check for help text when no platforms selected
    await expect(page.getByText('Need to connect accounts?')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Go to Accounts' })).toBeVisible();
  });

  test('should display media upload button', async ({ page }) => {
    await page.goto('/content/new');
    
    // Check for media upload button
    const mediaButton = page.getByRole('button', { name: /Media/i });
    await expect(mediaButton).toBeVisible();
  });

  test('should show content repurpose button when content and platforms selected', async ({ page }) => {
    await page.goto('/content/new');
    
    // Select platform and add content
    await page.getByRole('button', { name: /LinkedIn/i }).first().click();
    const textarea = page.getByPlaceholder('What do you want to share?');
    await textarea.fill('Test content for repurposing');
    
    // Check for repurpose button
    const repurposeButton = page.getByRole('button', { name: /Repurpose/i });
    await expect(repurposeButton).toBeVisible();
  });

  test('should show preview placeholder when no platform selected', async ({ page }) => {
    await page.goto('/content/new');
    
    // Check for preview placeholder
    await expect(page.getByText('Select a platform to see a live preview')).toBeVisible();
    await expect(page.getByText('Your post will appear here as you type')).toBeVisible();
  });
});