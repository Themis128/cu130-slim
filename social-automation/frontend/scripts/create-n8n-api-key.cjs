const { chromium } = require('playwright');

async function createN8nApiKey() {
  const browser = await chromium.launch({ headless: false, slowMo: 500 });
  const context = await browser.newContext();
  const page = await context.newPage();

  // Enable console logging
  page.on('console', msg => console.log('BROWSER:', msg.text()));
  page.on('pageerror', err => console.log('PAGE ERROR:', err.message));

  try {
    // Login to n8n
    await page.goto('http://localhost:5678');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(5000);
    
    // Debug: check if Vue app mounted
    const bodyContent = await page.textContent('body');
    console.log('Body text preview:', bodyContent.substring(0, 500));
    
    // Wait for Vue app to mount - look for n8n-specific elements
    await page.waitForSelector('[data-test-id], .n8n-app, #app, .app', { timeout: 60000 });
    console.log('App container found');
    await page.waitForTimeout(3000);
    
    // Fill login form
    await page.waitForSelector('input[type="email"], input[name="email"], input[placeholder*="email" i]', { timeout: 30000 });
    await page.fill('input[type="email"], input[name="email"], input[placeholder*="email" i]', 'admin@n8n.local');
    await page.fill('input[type="password"], input[name="password"], input[placeholder*="password" i]', 'secure_password');
    await page.click('button[type="submit"], button:has-text("Sign in"), button:has-text("Log in")');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(5000);
    
    // Navigate to Settings > API
    await page.goto('http://localhost:5678/settings/api');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(5000);
    
    // Debug
    const apiBody = await page.textContent('body');
    console.log('API page body:', apiBody.substring(0, 1000));
    
    // Wait for any button with "Create" text
    await page.waitForSelector('button:has-text("Create")', { timeout: 30000 });
    console.log('Create button found');
    
  } catch (error) {
    console.error('Error:', error);
    const content = await page.content();
    console.log('Page content at error:');
    console.log(content.substring(0, 15000));
  } finally {
    // Keep browser open for manual inspection
    // await browser.close();
  }
}

createN8nApiKey();