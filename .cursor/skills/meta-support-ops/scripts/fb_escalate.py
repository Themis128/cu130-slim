#!/usr/bin/env python3
"""Escalate each Meta issue through the contact-support wizard + AI chat on CDP :9330.
Usage: python fb-escalate.py <issue_key>  -> drives one issue, dumps transcript."""
import json, time, re, sys, os
from playwright.sync_api import sync_playwright

# :9330 = headless sidecar (needs its own FB session); :9229 = user's debug
# Chrome which currently holds the live FB session — override via env.
CDP = os.environ.get("CDP_URL", "http://localhost:9330")
OUT = os.environ.get("ESCALATE_OUT",
                     r"C:\Users\tbaltzakis\devin-scripts\fb-escalations")
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
        "text": ("Multiple compounding account-security failures on my Meta "
                 "Account (baltzakis.themis@gmail.com), all started after a "
                 "'Suspicious activity on your Meta Account — suspended for "
                 "your protection' alert: (1) Instagram account cloudless.gr "
                 "(https://www.instagram.com/cloudless.gr/) cannot complete "
                 "login 2FA — password accepted, but WhatsApp AND SMS codes "
                 "to the phone ending 38 never arrive over repeated attempts "
                 "across multiple days; five freshly generated Meta Account "
                 "recovery codes were all rejected (Instagram 2FA is a "
                 "separate enrollment), both stored TOTP seeds rejected, and "
                 "the emailed checkpoint link "
                 "auth.meta.com/checkpoints/429819665900096/ returns 404. "
                 "(2) Instagram password reset shows 'Something went wrong' "
                 "on every lane; the email login link does arrive but lands "
                 "on the same 2FA wall. (3) Facebook login works via "
                 "trusted-device approval, confirming the number itself is "
                 "reachable. Requesting: investigation of why Meta Account "
                 "codes are not delivered to this number, review/lift of "
                 "the protection suspension, and an account-recovery path "
                 "for Instagram cloudless.gr that does not depend on "
                 "receiving SMS/WhatsApp codes.")},
    "waba-verify-sms": {
        "asset": "WhatsApp Business phone +30 697 777 7838 (Greece) — WABA "
                 "1717901383017723, business portfolio 1558125105019725, "
                 "app 1936126137016578",
        "text": ("WhatsApp Business phone-number verification for +30 697 "
                 "777 7838 (Greece) is undeliverable: the verification SMS "
                 "and voice call never arrive. Last successfully delivered "
                 "WhatsApp code was 2026-11-24; every resend attempt since "
                 "then (SMS and 'call me' lanes, spread over days) produces "
                 "no message, while unrelated SMS reach the device normally "
                 "and the number receives calls fine. This looks like a "
                 "number-level verification throttle or a dispatch failure "
                 "on Meta's side, not a carrier or device issue. Requesting: "
                 "(1) investigate WhatsApp verification dispatch for this "
                 "number, (2) lift any number-level send throttle, (3) an "
                 "alternate verification path (voice call, email code, or "
                 "manual verification) so the number can complete "
                 "registration in WhatsApp Business / WABA "
                 "1717901383017723.")},
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
        # Cookie seeding is for the bare :9330 sidecar. On :9229 the live
        # session already exists — skip injection (stale jar would clobber it).
        ck = r"C:\Users\tbaltzakis\devin-scripts\fb-cookies.json"
        if "9229" not in CDP and os.path.exists(ck) and \
                "SKIP_COOKIES" not in os.environ:
            d = json.load(open(ck))
            ctx.add_cookies([{"name": k, "value": v, "domain": ".facebook.com",
                              "path": "/", "secure": True, "httpOnly": True,
                              "sameSite": "Lax"}
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
