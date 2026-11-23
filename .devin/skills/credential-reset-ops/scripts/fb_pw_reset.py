#!/usr/bin/env python3
"""fb_pw_reset.py — drive the Facebook "Forgot password" flow end-to-end.

Sidecar (facebook-browser-sidecar :9226) navigates the recovery flow:
  identify account -> code requested -> code self-reads off the phone
  (wa_verify lanes: ADB / Termux-SSH / mail-forward) or Gmail -> set new
  password -> FB_PASSWORD written to .env.

Usage:
  fb_pw_reset.py <new-password>          # full drive
  fb_pw_reset.py <new-password> --dry    # print steps, don't drive
"""
import argparse
import json
import os
import re
import subprocess
import time
import urllib.request

SIDECAR = "http://localhost:9226"
ENV_PATH = r"C:\Users\tbaltzakis\cu130-slim\.env"
def _gmail_secret(name):
    """%APPDATA% never expands under WSL — the real path is the
    Windows secrets dir via /mnt/c."""
    wsl = f"/mnt/c/Users/tbaltzakis/AppData/Roaming/devin/secrets/{name}"
    win = os.path.expandvars(rf"%APPDATA%\devin\secrets\{name}")
    for p in (wsl, win):
        if os.path.exists(p):
            return p
    return wsl


GMAIL_SECRETS = _gmail_secret("gmail-token.json")
GMAIL_KEYS = _gmail_secret("gcp-oauth.keys.json")
KEY = os.path.expanduser("~/.ssh/id_rsa_win")
PHONE_IP = os.environ.get("PHONE_ADB_IP", "192.168.1.77")
ADBS = ["adb",
        "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
        "/mnt/c/platform-tools/adb.exe"]
CODE_RE = re.compile(r"\b(\d{6})\b")


def req(method, path, payload=None, timeout=60):
    r = urllib.request.Request(
        SIDECAR + path,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read())


def ev(script, timeout=30):
    return req("POST", "/debug/eval", {"script": script}, timeout).get("result")


def page():
    return req("GET", "/debug/page-text", timeout=20).get("text", "")


def click(text):
    return ev(
        "(()=>{const b=[...document.querySelectorAll('button,a,div[role=button],span')]"
        f".find(e=>(e.innerText||'').trim().toLowerCase()==='{text.lower()}');"
        "if(b){b.click();return 'clicked'}return 'not-found'})()")


def fill(sel_value, sel="input[type=password],input[type=text],input[type=email]"):
    # React-safe: direct .value assignment is ignored — use the native
    # setter + input event or the form submits empty (2026-11-24).
    return ev(
        f"(()=>{{const i=document.querySelector({json.dumps(sel)});"
        "if(!i)return 'no-field';i.focus();"
        "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
        f"s.call(i,{json.dumps(sel_value)});"
        "i.dispatchEvent(new Event('input',{bubbles:true}));"
        "i.dispatchEvent(new Event('change',{bubbles:true}));return 'filled'})()")


def dismiss_consent():
    """The bottom-bar 'Allow the use of cookies' variant poisons Meta
    flows — trusted-click it (it's topmost), then strip pointer-events
    on its root as a fallback."""
    try:
        req("POST", "/debug/click", {"text": "Allow the use of cookies"}, 15)
    except Exception:
        pass
    strip_overlay()


def strip_overlay():
    """The consent root (xzkaem6 subtree) re-mounts on every render and
    intercepts every locator.click behind it — strip pointer-events."""
    ev("(()=>{[...document.querySelectorAll('div')]"
       ".filter(d=>/xzkaem6/.test(d.className))"
       ".forEach(d=>d.style.pointerEvents='none');return 1})()")


def tclick(text, timeout=15):
    """Trusted Playwright click for buttons that ignore synthetic
    .click() (submit buttons on identify/2FA surfaces) — strips the
    consent overlay first so the hit-test lands."""
    strip_overlay()
    try:
        r = req("POST", "/debug/click", {"text": text}, timeout)
        return "ok" if r.get("status") == "ok" else "fail"
    except Exception:
        return "fail"


def adb_code():
    for b in ADBS:
        try:
            r = subprocess.run(
                [b, "shell", "content", "query", "--uri", "content://sms/inbox",
                 "--projection", "address,date,body", "--sort", "date DESC"],
                capture_output=True, text=True, timeout=8)
            rows = [row for row in r.stdout.splitlines() if re.search(
                r'facebook|meta|AUTHMSG', row, re.I)]
            m = CODE_RE.search(" ".join(rows)) if rows else CODE_RE.search(r.stdout)
            if m:
                return m.group(1)
        except Exception:
            continue
    return None


def termux_code():
    try:
        r = subprocess.run(
            ["ssh", "-p", "8022", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=no", "-i", KEY,
             f"u0_a357@{PHONE_IP}", "termux-sms-list -l 5"],
            capture_output=True, text=True, timeout=12)
        rows = [row for row in r.stdout.splitlines() if re.search(
            r'facebook|meta|AUTHMSG', row, re.I)]
        m = CODE_RE.search(" ".join(rows)) if rows else CODE_RE.search(r.stdout)
        return m.group(1) if m else None
    except Exception:
        return None


def gmail_code(since_epoch=0):
    """FB recovery codes land in Gmail — decode newest security@ mail.
    `after:` filter uses the request timestamp so stale codes from
    earlier flows are never picked up."""
    try:
        import datetime
        tok = json.load(open(GMAIL_SECRETS))
        keys = json.load(open(GMAIL_KEYS))['installed']
        r = urllib.request.urlopen(urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({
                "client_id": keys["client_id"],
                "client_secret": keys["client_secret"],
                "refresh_token": tok["refresh_token"],
                "grant_type": "refresh_token"}).encode()))
        at = json.loads(r.read())["access_token"]
        q = "from:security@facebookmail.com"
        if since_epoch:
            q += " after:" + datetime.datetime.fromtimestamp(
                since_epoch).strftime("%Y/%m/%d")
            q += " newer_than:10m"
        else:
            q += " newer_than:10m"
        r = urllib.request.urlopen(urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages"
            f"?q={urllib.parse.quote(q)}&maxResults=2",
            headers={"Authorization": "Bearer " + at}))
        ids = [m["id"] for m in json.loads(r.read()).get("messages", [])]
        for mid in ids:
            r = urllib.request.urlopen(urllib.request.Request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}?format=full",
                headers={"Authorization": "Bearer " + at}))
            body = json.dumps(json.loads(r.read()))
            m = CODE_RE.search(body)
            if m:
                return m.group(1)
    except Exception as e:
        print("gmail err:", e)
    return None


def any_code(since_epoch=0):
    return adb_code() or termux_code() or gmail_code(since_epoch)


ENV_PATHS = (
    ENV_PATH,                                                   # Windows-side run
    os.path.expanduser("~/cu130-slim/.env"),                    # WSL checkout
    "/mnt/c/Users/tbaltzakis/cu130-slim/.env",                  # mounted checkout (feeds containers)
    os.path.expanduser("~/cu130-slim/deploy/socialauto-standalone/.env"),
)


def save_pw(pw):
    # .env append/update — FB_PASSWORD + FACEBOOK_PASSWORD slots across
    # every reachable checkout (heal lanes read FACEBOOK_PASSWORD).
    for path in ENV_PATHS:
        if not os.path.exists(path):
            continue
        lines = open(path).read().split("\n")
        out, seen = [], False
        for ln in lines:
            if ln.startswith(("FB_PASSWORD=", "FACEBOOK_PASSWORD=")):
                k = ln.split("=", 1)[0]
                out.append(f"{k}={pw}")
                seen = True
            else:
                out.append(ln)
        if not seen:
            out.append(f"FB_PASSWORD={pw}")
        open(path, "w").write("\n".join(out))
        print(f"password keys written to {path}")


def reset(new_pw, dry=False):
    """Navigate forgot-password -> identify -> code -> new password."""
    print("[1] navigate to recovery")
    if not dry:
        # /login/identify/ redirects to the profile picker when a saved-
        # profile cookie exists — the "Forgot password?" link only lives
        # on the email/pass form behind "Use another profile".
        req("POST", "/debug/navigate",
            {"url": "https://www.facebook.com/login"})
        time.sleep(8)
        dismiss_consent()
        click("use another profile")
        time.sleep(5)
        r = click("forgot password?") or click("forgotten password")
        print("  forgot-link:", r)
        time.sleep(8)
    print("  page:", page()[:100] if not dry else "(dry)")

    print("[2] identify account — tap Search/Continue after entering id")
    if not dry:
        dismiss_consent()
        ident = os.environ.get("FB_RESET_IDENT", "baltzakis.themis@gmail.com")
        r = fill(ident)  # email — the identify page takes email/phone/username
        print("  ident fill:", r)
        if click("Search") == "not-found" and click("Continue") == "not-found":
            tclick("Search") or tclick("Continue")
        else:
            tclick("Continue")  # synthetic 'clicked' is a lie on this surface
        time.sleep(6)
        print("  post-ident:", page()[:90])

    print("[3] request the code")
    req_ts = time.time()
    if not dry:
        # "Choose a way to log in" — pick the email code option first
        # (it is a row/label, not a button); then Continue.
        ev("(()=>{const b=[...document.querySelectorAll('label,div[role=button],div[role=radio],span')]"
           ".find(e=>/get code via email|send code via email|email/i.test(e.innerText||'')&&(e.innerText||'').length<80);"
           "if(b){b.click();return 'clicked'}return 'none'})()")
        time.sleep(3)
        if not (click("Send code") or click("Continue") or click("Get code")):
            pass
        tclick("Send code") or tclick("Continue") or tclick("Get code")
        time.sleep(8)
        print("  code-requested state:", page()[:100])

    print("[4] poll the code off the phone / gmail (90s)")
    code = None
    if not dry:
        for _ in range(9):
            code = any_code(req_ts)
            if code:
                break
            time.sleep(10)
        print("  code:", code or "NONE — enter manually")

    print("[5] enter code + new password")
    if not dry and code:
        fill(code)
        tclick("Continue")
        time.sleep(6)
        fill(new_pw)
        tclick("Continue")
        time.sleep(6)
        print("  post-reset page:", page()[:100])
        save_pw(new_pw)
        print("done — FB password reset to new value + stored")
    elif not dry:
        print("no code — drive manually from the recovery page")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("password")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    reset(a.password, a.dry)
