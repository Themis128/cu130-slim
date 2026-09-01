// === Facebook Helper Init Script ===
// Only safe, non-contradictory patches. No WebGL spoofing (Facebook detects
// the mismatch between spoofed renderer string and actual SwiftShader pixels).

(function() {
  // 1. Mask the automation flag at the JS level (browser flag handles the rest)
  try { Object.defineProperty(navigator, 'webdriver', { get: () => false }); } catch(e) {}

  // 2. Fix window.chrome object for Chromium
  if (!window.chrome) { window.chrome = { runtime: {} }; }

  // 3. Override permissions API to return real notification state
  const originalQuery = navigator.permissions && navigator.permissions.query;
  if (originalQuery) {
    navigator.permissions.query = function(params) {
      if (params.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission, onchange: null });
      }
      return originalQuery.call(navigator.permissions, params);
    };
  }

  // 4. Auto-dismiss Facebook cookie-consent dialogs (with retries)
  function dismissCookieDialog() {
    const buttons = Array.from(document.querySelectorAll('div[role="button"], button'));
    const allowAll = buttons.find(b => b.textContent.trim() === 'Allow all cookies');
    if (allowAll) { allowAll.click(); return true; }
    const decline = buttons.find(b => b.textContent.trim() === 'Decline optional cookies');
    if (decline) { decline.click(); return true; }
    return false;
  }
  setTimeout(dismissCookieDialog, 1000);
  setTimeout(dismissCookieDialog, 3000);
  setTimeout(dismissCookieDialog, 5000);
})();
