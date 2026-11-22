#!/usr/bin/env python3
"""Escalate each Meta issue through the contact-support wizard + AI chat on CDP :9330.
Usage: python fb-escalate.py <issue_key>  -> drives one issue, dumps transcript."""
import json, time, re, sys
from playwright.sync_api import sync_playwright

CDP = "http://localhost:9330"
OUT = r"C:\Users\tbaltzakis\devin-scripts\fb-escalations"
import os; os.makedirs(OUT, exist_ok=True)

ISSUES = {
    "personal-restriction": {
        "asset": "1134463867 personal account",
        "text": ("My personal Meta account 1134463867 carries an advertising restriction "
                 "since 2021-02-12: 'You can't manage advertising assets or people for "
                 "businesses'. Account Quality shows no review button. This restriction now "
                 "blocks Business Settings mutations in portfolio 1558125105019725 "
                 "(cloudless.gr). Requesting a manual review and lift of this standing "
                 "restriction.")},
    "portfolio-mutation": {
        "asset": "1558125105019725 cloudless.gr business portfolio",
        "text": ("Business portfolio 1558125105019725 (cloudless.gr). After a Facebook "
                 "password change all Meta API tokens were invalidated (OAuth 190/460). "
                 "I need to add app 1936126137016578 and create a System User to mint a "
                 "new WhatsApp Business API token for WABA 1717901383017723. Business "
                 "Settings > Accounts > Apps returns 'Your account is restricted right "
                 "now - temporarily blocked from performing this action'. Requesting "
                 "manual review to lift the temporary restriction.")},
    "dead-ad-accounts": {
        "asset": "657781691826702 ad account",
        "text": ("Ad accounts 657781691826702 and 2771400643144250 under business "
                 "portfolio 1558125105019725 were disabled January 2021 and the review "
                 "window expired ('too much time has passed since this account was "
                 "disabled, so this decision can't be reviewed'). Requesting either a "
                 "manual re-review OR written confirmation that creating a NEW ad "
                 "account is the supported path, and that no flags from the dead "
                 "accounts carry over to it.")},
    "waba-system-user": {
        "asset": "1558125105019725 cloudless.gr business portfolio",
        "text": ("I need to create a System User in business portfolio 1558125105019725 "
                 "to mint a never-expiring WhatsApp Business API token for WABA "
                 "1717901383017723 (app 1936126137016578). Business Settings > System "
                 "Users shows the 'Add' button disabled because of the account "
                 "restriction. Requesting manual unblock or admin-side system user "
                 "creation so our WhatsApp Business API lane can be restored.")},
    "meta-2fa-codes": {
        "asset": "Instagram account cloudless.gr — profile URL "
                 "https://www.instagram.com/cloudless.gr/ (personal Meta "
                 "account baltzakis.themis@gmail.com)",
        "text": ("Instagram account cloudless.gr (profile URL "
                 "https://www.instagram.com/cloudless.gr/) cannot complete "
                 "login two-factor "
                 "authentication: the password is accepted and Meta offers "
                 "confirmation codes via WhatsApp and SMS to the phone number "
                 "ending 38, but NO code ever arrives on either channel "
                 "(verified over repeated attempts across multiple days). "
                 "Authentication-app TOTP codes from the stored seed are rejected, "
                 "previously generated backup codes no longer work, and "
                 "trusted-device approval succeeded for Facebook but the "
                 "Instagram challenge persists. Requesting: (1) investigation of "
                 "why WhatsApp/SMS 2FA codes are not delivered to this number, "
                 "and (2) an account-recovery path that does not depend on "
                 "receiving those codes.")},
}

def snap_text(page):
    try:
        return re.sub(r"\s+", " ", (page.evaluate("document.body.innerText") or ""))
    except Exception:
        time.sleep(3)
        try:
            return re.sub(r"\s+", " ", (page.evaluate("document.body.innerText") or ""))
        except Exception as e:
            return f"<eval-failed: {e}>"

def click_any(page, pat, timeout=4000):
    try:
        page.get_by_text(re.compile(pat, re.I), exact=False).first.click(timeout=timeout)
        return True
    except Exception:
        return False

def drive(key, page):
    iss = ISSUES[key]
    transcript = [f"===== ESCALATION {key} ====="]
    page.goto("https://www.facebook.com/business-support-home/contact-support",
              wait_until="domcontentloaded", timeout=60000)
    time.sleep(8)
    transcript.append("0-START: " + snap_text(page)[:600])

    for gate in range(10):
        body = snap_text(page)
        if "switch to" in body.lower() or "switching accounts" in body.lower():
            page.evaluate("""(()=>{const el=Array.from(document.querySelectorAll('[role=button]')).find(e=>/themistoklis/i.test(e.innerText||'')); if(el){el.click(); return 'card'} return null})()""")
            time.sleep(3)
            page.evaluate("(()=>{const el=Array.from(document.querySelectorAll('[role=button],button')).find(e=>/^continue$/i.test((e.innerText||'').trim())); if(el)el.click()})()")
            time.sleep(6)
            if "contact-support" not in page.url:
                page.goto("https://www.facebook.com/business-support-home/contact-support",
                          wait_until="domcontentloaded", timeout=60000)
                time.sleep(7)
            continue
        if page.evaluate("!!document.querySelector('input[type=text],input:not([type]),textarea')"):
            transcript.append(f"wizard-reached at gate {gate}")
            break
        if "contact-support" not in page.url:
            page.goto("https://www.facebook.com/business-support-home/contact-support",
                      wait_until="domcontentloaded", timeout=60000)
            time.sleep(7)
            continue
        click_any(page, r"^Continue$|^Next$|^Get started$")
        time.sleep(5)

    full = iss["asset"] + " -- " + iss["text"]
    try:
        page.locator("input[type=text], input:not([type]), textarea").first.click(timeout=8000)
    except Exception:
        click_any(page, "Tell us your asset")
    time.sleep(1)
    ok = page.evaluate(
        """(txt)=>{const i=document.querySelector('input[type=text],input:not([type]),textarea');
        if(!i)return 'no-input'; const p=Object.getPrototypeOf(i);
        const s=Object.getOwnPropertyDescriptor(p,'value').set; s.call(i,txt);
        i.dispatchEvent(new Event('input',{bubbles:true}));
        i.dispatchEvent(new Event('change',{bubbles:true})); return i.value.slice(0,40)}""",
        full)
    transcript.append(f"fill-> {ok!r}")
    time.sleep(2)
    try:
        page.locator("[role=option], [role=listbox] >> *").first.click(timeout=3000)
    except Exception:
        pass
    nxt = page.evaluate("(()=>{const el=Array.from(document.querySelectorAll('[role=button],button')).find(e=>/^(Next|Submit|Send|Continue)$/i.test((e.innerText||'').trim())); if(el){el.scrollIntoView(); el.click(); return (el.innerText||'').trim()} return null})()")
    if not nxt:
        page.keyboard.press("Enter")
    time.sleep(8)
    transcript.append("1-NEXT: " + snap_text(page)[:600])

    try:
        page.locator("textarea, input[placeholder*='details'], [role=textbox]").first.fill(
            iss["text"], timeout=6000)
    except Exception:
        try:
            page.locator("[role=textbox]").first.click(timeout=3000)
            page.keyboard.type(iss["text"], delay=5)
        except Exception:
            transcript.append("!! description field not found")
    click_any(page, r"^Next$|^Submit$|^Send$|^Continue$")
    time.sleep(6)
    transcript.append("2-DESC: " + snap_text(page)[:600])

    for i in range(4):
        click_any(page, r"^Next$|^Submit$|^Send$|^Continue$|^Confirm$|^Take action$|^Request review$")
        time.sleep(6)
        transcript.append(f"3-{i}: " + snap_text(page)[:500])

    # Meta AI chat: consent gate then textbox
    click_any(page, r"^Continue$|^Get started$")
    time.sleep(4)
    tb = page.locator("div[role=textbox], [role=textbox]").first
    try:
        tb.click(timeout=8000)
        page.keyboard.type(iss["text"], delay=4)
        page.keyboard.press("Enter")
        transcript.append("CHAT-SENT")
    except Exception as e:
        transcript.append(f"!! chat textbox failed: {e}")
    for i in range(4):
        time.sleep(12)
        transcript.append(f"CHAT-{i}: " + snap_text(page))
    return "\n".join(transcript)

def main(key):
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0]
        d = json.load(open(r"C:\Users\tbaltzakis\devin-scripts\fb-cookies.json"))
        ctx.add_cookies([{"name": k, "value": v, "domain": ".facebook.com", "path": "/",
                          "secure": True, "httpOnly": True, "sameSite": "Lax"}
                         for k, v in d["cookies"].items()])
        page = ctx.new_page()
        ts = drive(key, page)
        outp = os.path.join(OUT, f"{key}.log")
        open(outp, "w", encoding="utf-8").write(ts)
        try:
            page.screenshot(path=os.path.join(OUT, f"{key}.png"), full_page=False)
        except Exception:
            pass
        print(ts[-2000:])
        print(f"\n>>> saved {outp}")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "portfolio-mutation")
