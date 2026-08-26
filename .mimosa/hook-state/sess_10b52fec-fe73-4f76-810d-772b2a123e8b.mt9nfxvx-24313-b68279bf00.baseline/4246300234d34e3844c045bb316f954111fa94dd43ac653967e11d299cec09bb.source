import { chromium } from 'playwright';

async function createN8nApiKey() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    // Login to n8n
    await page.goto('http://localhost:5678');
    await page.waitForLoadState('networkidle');
    
    // Fill login form
    await page.fill('input[name="email"]', 'admin@n8n.local');
    await page.fill('input[name="password"]', 'secure_password');
    await page.click('button[type="submit"]');
    await page.waitForLoadState('networkidle');
    
    // Navigate to Settings > API
    await page.goto('http://localhost:5678/settings/api');
    await page.waitForLoadState('networkidle');
    
    // Click "Create API key" button
    await page.click('button:has-text("Create API key")');
    await page.waitForLoadState('networkidle');
    
    // Fill in the label
    await page.fill('input[name="label"]', 'social-automation-api-key');
    
    // Set expiry to 1 year
    const expiryDate = new Date();
    expiryDate.setFullYear(expiryDate.getFullYear() + 1);
    const expiryString = expiryDate.toISOString().split('T')[0];
    await page.fill('input[name="expiresAt"]', expiryString);
    
    // Select all workflow scopes
    const scopes = [
      'workflow:create',
      'workflow:read', 
      'workflow:execute',
      'workflow:list',
      'workflow:update',
      'workflow:delete',
      'workflow:activate'
    ];
    
    for (const scope of scopes) {
      await page.check(`input[value="${scope}"]`);
    }
    
    // Click create
    await page.click('button:has-text("Create")');
    await page.waitForLoadState('networkidle');
    
    // Get the generated API key
    const apiKey = await page.textContent('[data-test-id="api-key-display"]') 
      || await page.textContent('code') 
      || await page.textContent('.api-key');
    
    if (apiKey) {
          // API key generated successfully - not logging for security
    } else {
      // Try to find it in the page content
      const content = await page.content();
      const match = content.match(/n8n_api_[a-f0-9]+/);
      if (match) {
            // API key generated successfully - not logging for security
      }
    }
    
  } catch (error) {
    console.error('Error:', error);
  } finally {
    await browser.close();
  }
}

createN8nApiKey();