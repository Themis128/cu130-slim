// Auto-dismiss Facebook cookie consent dialogs
(function() {
  function dismissCookieDialog() {
    // Click "Allow all cookies" button on Facebook
    const buttons = Array.from(document.querySelectorAll('div[role="button"], button'));
    const allowAll = buttons.find(b => b.textContent.trim() === 'Allow all cookies');
    if (allowAll) { allowAll.click(); return true; }
    // Fallback: "Decline optional cookies"
    const decline = buttons.find(b => b.textContent.trim() === 'Decline optional cookies');
    if (decline) { decline.click(); return true; }
    return false;
  }
  // Try immediately and again after delays
  setTimeout(dismissCookieDialog, 1000);
  setTimeout(dismissCookieDialog, 3000);
  setTimeout(dismissCookieDialog, 5000);
})();

// Helper: fill React-controlled inputs properly
window.__pwFillReact = function(selector, value) {
  const input = document.querySelector(selector);
  if (!input) return 'element not found';
  const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  nativeSetter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return 'filled: ' + input.value;
};
