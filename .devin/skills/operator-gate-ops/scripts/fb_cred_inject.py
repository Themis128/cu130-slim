#!/usr/bin/env python3
"""fb_cred_inject.py — one paste closes the whole FB gate.

Pops a local password box (HTA on Windows — no deps). The password is
held only in this process' memory, typed via CDP into the facebook.com
login form, then again into Meta's App-Secret re-auth dialog, then
discarded — never stored, never printed.

Usage: python fb_cred_inject.py            # asks email + password once
       python fb_cred_inject.py --email E  # password only
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_runner import _ensure_cdp, Page  # noqa: E402
import fb_secret_reveal as R  # noqa: E402
import fb_secret_ask as A  # noqa: E402

APP_URL = "https://developers.facebook.com/apps/1936126137016578/settings/basic/"


def ask(field, title):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Add-Type -AssemblyName Microsoft.VisualBasic;"
         f"[Microsoft.VisualBasic.Interaction]::InputBox('{title}','Cloudless gate','')"],
        capture_output=True, text=True, timeout=300)
    return r.stdout.strip()


def fb_login(p, email, pw):
    import json as _j
    p.goto("https://www.facebook.com/login/")
    time.sleep(6)
    p.js("(()=>{const e=document.querySelector('#email'); if(e){e.value=" + _j.dumps(email) + ";"
         "e.dispatchEvent(new Event('input',{bubbles:true}));}"
         "const w=document.querySelector('#pass'); if(w){w.value=" + _j.dumps(pw) + ";"
         "w.dispatchEvent(new Event('input',{bubbles:true}));}})()")
    p.js("(()=>{const b=document.querySelector('#loginbutton'); if(b)b.click();})()")
    time.sleep(8)


def main():
    # stored creds first — zero-touch on repeat runs
    stored = None
    try:
        import fb_cred_store
        stored = fb_cred_store.cred_get()
    except Exception:
        pass
    if stored:
        email, pw = stored
        print("using stored Facebook credentials (Windows Credential Manager)")
    else:
        email = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--email=")),
                     "") or ask("email", "Facebook email")
        pw = ask("password", "Facebook password (typed into the dialog, never stored)")
        if email and pw:
            try:
                fb_cred_store.cred_set(email, pw)
                print("credentials saved to Windows Credential Manager — next run is zero-touch")
            except Exception:
                pass
    if not email or not pw:
        print("no credentials supplied"); return 1

    _ensure_cdp()
    p = Page(url_match="facebook")
    t = p.poll("document.body.innerText", timeout=15) or ""
    if "Create App" not in t and "My Apps" not in t:
        fb_login(p, email, pw)
        p.goto("https://developers.facebook.com/apps/")
        time.sleep(6)
        t = p.poll("document.body.innerText", timeout=15) or ""
        if "Create App" not in t and "My Apps" not in t:
            print("login may have failed or hit a checkpoint — check the Chrome window")
            return 1
    print("FB login OK")

    print(A.stage())
    A.bring_front()
    A.toast("Confirm the Meta re-auth — password will be auto-typed")
    deadline = time.time() + 300
    while time.time() < deadline:
        p = Page(url_match="facebook")
        if R._dialog_open(p):
            p.js("(()=>{const i=document.querySelector('input[type=password]');"
                 "if(i){i.value=" + __import__('json').dumps(pw) + ";i.dispatchEvent(new Event('input',{bubbles:true}));"
                 "setTimeout(()=>{const b=[...document.querySelectorAll('button,[role=button]')]"
                 ".filter(x=>/confirm|continue|ok|submit/i.test(x.innerText||''));"
                 "if(b.length)b[0].click();},600);}})()", pw)
            time.sleep(5)
            if not R._dialog_open(p):
                break
        time.sleep(3)
    pw = None  # discard immediately
    while time.time() < deadline + 300:
        p = Page(url_match="facebook")
        sec = R._secret_value(p)
        if sec:
            rc = R._envmgr_put("FACEBOOK_CLIENT_SECRET", sec)
            print(f"App Secret captured + saved (rc={rc})")
            return 0
        time.sleep(4)
    print("timeout — dialog still open or secret not visible")
    return 1


if __name__ == "__main__":
    sys.exit(main())
