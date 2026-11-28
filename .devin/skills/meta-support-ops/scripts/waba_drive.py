# -*- coding: utf-8 -*-
"""waba_drive.py — finish the contact-support wizard for waba-verify-sms on :9330."""
import time, re, sys
from playwright.sync_api import sync_playwright

CDP = "http://localhost:9330"
CASE_TEXT = (
    "WABA 1717901383017723, phone +30 697 777 7838 Greece, portfolio 1558125105019725: "
    "WhatsApp Business phone-number verification SMS and voice call never arrive. "
    "Last delivered code was 2026-11-24; every resend attempt since produces no message "
    "while other SMS arrive normally. Looks like a number-level verification throttle or "
    "dispatch failure on Meta side. Requesting investigation of code dispatch for this "
    "number, lift of any send throttle, or an alternate verification path (voice call, "
    "email code, or manual verification)."
)

def snap(page, n=400):
    try:
        return re.sub(r"\s+", " ", (page.evaluate("document.body.innerText") or ""))[:n]
    except Exception:
        return "<no-body>"

def find_page(b):
    for ctx in b.contexts:
        for p in ctx.pages:
            if "facebook.com" in p.url:
                yield p

pw = sync_playwright().start()
b = pw.chromium.connect_over_cdp(CDP)
page = None
for _ in range(15):
    for p in find_page(b):
        if "contact-support" in p.url:
            page = p
    if page:
        try:
            if page.evaluate("!!document.body"):
                break
        except Exception:
            pass
    time.sleep(3)
if not page:
    print("no contact-support page found on :9330")
    pw.stop()
    sys.exit(1)
print("page:", page.url[:100])

# Fill the describe-issue input
for attempt in range(6):
    try:
        n = page.evaluate("document.querySelectorAll('input[type=text]').length")
        if n:
            page.evaluate("""(v) => {
              const inp = document.querySelector('input[type=text]');
              Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(inp, v);
              inp.dispatchEvent(new Event('input',{bubbles:true}));
              inp.dispatchEvent(new Event('change',{bubbles:true}));
            }""", CASE_TEXT)
            print("filled:", page.evaluate("document.querySelector('input[type=text]').value.length"))
            break
    except Exception as e:
        print("fill retry", attempt, str(e)[:80])
        time.sleep(4)

page.evaluate("""(() => {
  const b = [...document.querySelectorAll('div[role=button],button')].find(e => (e.innerText||'').trim() === 'Next');
  if (b) b.click();
})()""")
print("next clicked")
time.sleep(8)
print("STATE:", snap(page, 800))
pw.stop()
