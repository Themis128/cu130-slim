#!/usr/bin/env python3
"""sidecar_fb_login.py — restore the facebook-browser-sidecar (:9226) FB session
through the full login+2FA chain, via the sidecar's own /debug API.

Run INSIDE social-api(-green):
    docker exec -e SOCIAL_API_INTERNAL=http://localhost:8000 \
        social-api-green python /app/scripts/../../scripts/sidecar_fb_login.py
(or copy into /app/scripts first; bind-mounted from the repo checkout)

Env:
    FB_LOGIN_EMAIL      — account email        (required)
    FB_LOGIN_PASSWORD   — account password     (required)
    FB_BACKUP_CODE      — optional Meta backup code (8 digits, no space)
    FACEBOOK_SIDECAR    — default http://facebook-browser-sidecar:9226
    WAIT_APPROVAL_S     — seconds to wait for "Notification on another device"
                          approval on the user's Windows FB app (default 300)

State machine (verified 2026-11-14):
    profile picker → "Use another profile" → email+pass form → Meta-account
    password step → 2FA method picker → "Notification on another device"
    (label click + last Continue) → approval on Windows FB app → logged in.
    Passkey option hangs headless (no platform authenticator, QR never
    renders); WhatsApp/SMS codes go to +**38 (office SIM, not the phone).
Exit: 0 logged in, 3 human gate, 4 failed.
"""
import json, os, sys, time, urllib.request, urllib.error

SB = os.environ.get("FACEBOOK_SIDECAR", "http://facebook-browser-sidecar:9226")
EMAIL = os.environ.get("FB_LOGIN_EMAIL", "")
PASSWORD = os.environ.get("FB_LOGIN_PASSWORD", "")
BACKUP = os.environ.get("FB_BACKUP_CODE", "")
WAIT = int(os.environ.get("WAIT_APPROVAL_S", "300"))


def post(path, payload, timeout=60):
    r = urllib.request.Request(
        SB + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        return {"err": e.code, "body": e.read()[:200].decode("utf-8", "replace")}


def ev(script):
    return post("/debug/eval", {"script": script}).get("result")


def page_text():
    try:
        d = json.loads(urllib.request.urlopen(SB + "/debug/page-text", timeout=60).read())
        return d.get("url", ""), d.get("text", "")
    except Exception as e:
        return "", f"ERR:{e}"


def click_label(substr, tags='button,div[role=button],a'):
    js = ("(function(){var els=[].slice.call(document.querySelectorAll('%s'));"
          "var b=els.find(function(e){return /%s/i.test(e.innerText||e.value||e.getAttribute('aria-label')||'')});"
          "if(b){b.click();return 'clicked'}return 'none'})()" % (tags, substr))
    return ev(js)


def click_last_continue():
    js = ("(function(){var els=[].slice.call(document.querySelectorAll('button,div[role=button]'));"
          "var bs=els.filter(function(e){return /^continue$/i.test((e.innerText||'').trim())});"
          "var b=bs[bs.length-1];if(b){b.click();return 'clicked'}return 'none'})()")
    return ev(js)


def set_input(sel, val):
    js = ("(function(){var i=document.querySelector(%s);if(!i)return 'NOINPUT';"
          "var s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
          "i.focus();s.call(i,%s);i.dispatchEvent(new Event('input',{bubbles:true}));"
          "i.dispatchEvent(new Event('change',{bubbles:true}));return 'TYPED'})()"
          % (json.dumps(sel), json.dumps(val)))
    return ev(js)


def logged_in(text):
    return ("Notifications" in text or "Home" in text[:200]
            or "What's on your mind" in text or "News Feed" in text)


def wait_login(deadline):
    while time.time() < deadline:
        url, text = page_text()
        if logged_in(text):
            return True
        time.sleep(5)
    return False


def main():
    # 0. already logged in?
    url, text = page_text()
    if logged_in(text) and "login" not in url:
        print("already logged in"); return 0

    # 1. land on the login page fresh
    post("/debug/navigate", {"url": "https://www.facebook.com/login"}, 90)
    time.sleep(6)
    url, text = page_text()
    if logged_in(text):
        print("logged in after navigate"); return 0

    # 2. profile picker → "Use another profile" → email/pass form
    click_label("use another profile")
    time.sleep(5)
    r = set_input("input[name=email],input[type=email],input#email", EMAIL)
    if r != "TYPED":
        # meta-account password step may come first — fill password only
        r2 = set_input("input[type=password],input[name=pass]", PASSWORD)
        print("pw-only step:", r2)
        click_label("log in")
        time.sleep(8)
    else:
        set_input("input[name=pass],input[type=password],input#pass", PASSWORD)
        click_label("log in")
        time.sleep(10)

    url, text = page_text()
    if logged_in(text):
        print("logged in w/o 2fa"); return 0

    # 3. password step on meta-account gate (if it appears now)
    if "password" in text.lower() and "log in" in text.lower():
        set_input("input[type=password]", PASSWORD)
        click_label("log in")
        time.sleep(8)

    # 4. 2FA — prefer "Notification on another device" (Windows FB app)
    click_label("try another way")
    time.sleep(6)
    if click_label("notification on another device", "label") == "clicked":
        click_last_continue()
        print("awaiting approval on Windows FB app (up to %ds)" % WAIT)
        if wait_login(time.time() + WAIT):
            print("approved — logged in"); return 0
        print("approval timed out")

    # 5. fallback: backup code
    if BACKUP:
        click_label("try another way")
        time.sleep(5)
        click_label("backup code", "label")
        click_last_continue()
        time.sleep(5)
        set_input("input", BACKUP)
        click_label("continue|verify|confirm")
        time.sleep(8)
        if wait_login(time.time() + 30):
            print("backup code accepted — logged in"); return 0

    url, text = page_text()
    print("FAILED — url:", url[:120]); print(text[:400])
    return 4


if __name__ == "__main__":
    if not EMAIL or not PASSWORD:
        sys.exit("set FB_LOGIN_EMAIL + FB_LOGIN_PASSWORD")
    sys.exit(main())
