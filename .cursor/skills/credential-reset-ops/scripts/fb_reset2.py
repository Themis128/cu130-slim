#!/usr/bin/env python3
"""fb_reset2.py — Meta Account password reset via facebook.com/login/identify.

The Meta Account password is shared across FB/IG/Threads profiles, so this
single reset heals every stale Meta credential lane. Runs in a FRESH
Playwright context (logged-out) so /login/identify actually renders.

Code lanes (priority): ADB SMS inbox on the S22+ -> Termux SMS -> Gmail.
Writes the new password back to ~/cu130-slim/.env as FACEBOOK_PASSWORD and
INSTAGRAM_PASSWORD (same Meta Account secret).

Usage:
    fb_reset2.py [new-password] [--dry]
Never prints the password or codes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets as _secrets
import string
import subprocess
import sys
import time
import urllib.parse
import urllib.request

REPO = os.path.expanduser("~/cu130-slim")
ENV_FILE = os.path.join(REPO, ".env")
IDENTIFY_URL = "https://www.facebook.com/login/identify/"
PHONE = "+306977777838"
EMAIL = "baltzakis.themis@gmail.com"
CODE_RE = re.compile(r"\b(\d{6})\b")
ADBS = ["adb",
        "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe"]
GMAIL_SECRETS = "/mnt/c/Users/tbaltzakis/AppData/Roaming/devin/secrets/gmail-token.json"
GMAIL_KEYS = "/mnt/c/Users/tbaltzakis/AppData/Roaming/devin/secrets/gcp-oauth.keys.json"


def _pwlibs_env() -> dict:
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = os.path.expanduser(
        "~/.local/pwlibs/root/usr/lib/x86_64-linux-gnu"
    ) + ":" + env.get("LD_LIBRARY_PATH", "")
    return env


def chrome_exe() -> str | None:
    cache = os.path.expanduser("~/.cache/ms-playwright")
    revs = sorted(
        (d for d in os.listdir(cache) if d.startswith("chromium-")),
        key=lambda d: int(d.rsplit("-", 1)[1]))
    for rev in reversed(revs):
        cand = os.path.join(cache, rev, "chrome-linux64", "chrome")
        if os.path.exists(cand):
            return cand
    return None


def _txt(page) -> str:
    try:
        return page.evaluate("() => document.body.innerText") or ""
    except Exception:
        return ""


def _gmail_access_token() -> str | None:
    try:
        tok = json.load(open(GMAIL_SECRETS))
        keys = json.load(open(GMAIL_KEYS))["installed"]
        r = urllib.request.urlopen(urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({
                "client_id": keys["client_id"],
                "client_secret": keys["client_secret"],
                "refresh_token": tok["refresh_token"],
                "grant_type": "refresh_token"}).encode()), timeout=15)
        return json.loads(r.read())["access_token"]
    except Exception:
        return None


def gmail_search(q: str, regex: re.Pattern) -> str | None:
    at = _gmail_access_token()
    if not at:
        return None
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={urllib.parse.quote(q)}&maxResults=3",
            headers={"Authorization": "Bearer " + at}), timeout=15)
        for msg in json.loads(r.read()).get("messages", []):
            r2 = urllib.request.urlopen(urllib.request.Request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}?format=full",
                headers={"Authorization": "Bearer " + at}), timeout=15)
            body = json.dumps(json.loads(r2.read()))
            hit = regex.search(body)
            if hit:
                return hit.group(1)
    except Exception:
        pass
    return None


def dovecot_search(sender: str, regex: re.Pattern) -> str | None:
    user = "tbaltzakis@cloudless.gr"
    cmd = [
        "ssh", "omv-ha",
        f"for b in INBOX Junk; do sudo -n doveadm fetch -u {user} 'uid body' "
        f"mailbox $b FROM {sender} SENTSINCE 30m 2>/dev/null; done",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=60).stdout
    except Exception:
        return None
    for blk in reversed(re.split(r"(?=uid: \d+)", out)):
        hit = regex.search(blk)
        if hit:
            return hit.group(1)
    return None


def snap(page, tag):
    print(f"--- {tag} url={page.url}")
    print(_txt(page).replace("\n", " | ")[:450])


def click_text(page, texts, timeout=3000):
    for t in texts:
        try:
            loc = page.get_by_text(t, exact=False).first
            if loc.count() and loc.is_visible(timeout=timeout):
                try:
                    loc.click(timeout=2500)
                except Exception:
                    loc.dispatch_event("click")
                return t
        except Exception:
            continue
    return None


def fill_first(page, value):
    for sel in ('input[type="text"]', 'input[type="email"]',
                'input[name="email"]', 'input[autocomplete="username"]',
                'input'):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=2000):
                loc.click(timeout=2000)
                loc.press_sequentially(value, delay=15)
                return sel
        except Exception:
            continue
    return None


def gen_password() -> str:
    alpha = string.ascii_letters + string.digits
    return "Cl@" + "".join(_secrets.choice(alpha) for _ in range(14)) + "!"


def save_env_pw(pw: str) -> None:
    lines = open(ENV_FILE).read().splitlines()
    out, seen = [], set()
    for ln in lines:
        if ln.startswith(("FACEBOOK_PASSWORD=", "INSTAGRAM_PASSWORD=")):
            key = ln.split("=", 1)[0]
            out.append(f"{key}={pw}")
            seen.add(key)
        else:
            out.append(ln)
    for key in ("FACEBOOK_PASSWORD", "INSTAGRAM_PASSWORD"):
        if key not in seen:
            out.append(f"{key}={pw}")
    open(ENV_FILE, "w").write("\n".join(out) + "\n")
    os.chmod(ENV_FILE, 0o600)
    print("password written to .env (FACEBOOK_PASSWORD + INSTAGRAM_PASSWORD)")


# ── code lanes ─────────────────────────────────────────────────────────

def adb_code(since_ts: float) -> str | None:
    for b in ADBS:
        try:
            r = subprocess.run(
                [b, "shell", "content", "query", "--uri",
                 "content://sms/inbox", "--projection", "address,date,body",
                 "--sort", "date DESC"],
                capture_output=True, text=True, timeout=10)
            for row in r.stdout.splitlines()[:15]:
                if re.search(r"facebook|meta|authmsg|\bFB\b", row, re.I):
                    m = CODE_RE.search(row)
                    if m:
                        return m.group(1)
        except Exception:
            continue
    return None


def termux_code(since_ts: float = 0) -> str | None:
    try:
        key = os.path.expanduser("~/.ssh/id_rsa_win")
        r = subprocess.run(
            ["ssh", "-p", "8022", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=no", "-i", key,
             "u0_a357@192.168.1.77", "termux-sms-list -l 8"],
            capture_output=True, text=True, timeout=15)
        for row in r.stdout.splitlines():
            if re.search(r"facebook|meta|authmsg", row, re.I):
                m = CODE_RE.search(row)
                if m:
                    return m.group(1)
    except Exception:
        return None
    return None


def poll_code() -> str | None:
    deadline = time.time() + 240
    seen = set()
    while time.time() < deadline:
        for lane in (adb_code, termux_code):
            c = lane(0)
            if c and c not in seen:
                seen.add(c)
                return c
        c = gmail_search("from:facebookmail.com newer_than:8m", CODE_RE)
        if c and c not in seen:
            seen.add(c)
            return c
        c = dovecot_search("facebook", CODE_RE)
        if c and c not in seen:
            seen.add(c)
            return c
        time.sleep(8)
    return None


# ── drive ──────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("new_password", nargs="?", default=None)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--profile", default="/tmp/fb-reset-profile")
    ap.add_argument("--ident", default=PHONE, help="phone or email to identify")
    args = ap.parse_args()
    new_pw = args.new_password or gen_password()

    from playwright.sync_api import sync_playwright

    env_pw = _pwlibs_env()
    os.environ["LD_LIBRARY_PATH"] = env_pw["LD_LIBRARY_PATH"]

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            args.profile, executable_path=chrome_exe(), headless=False,
            viewport={"width": 1280, "height": 900}, locale="en-US",
            env=env_pw,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(IDENTIFY_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        snap(page, "identify")

        if args.dry:
            print("dry-run stop")
            ctx.close()
            return 0

        fill_first(page, args.ident)
        page.wait_for_timeout(500)
        click_text(page, ["Search", "Continue", "Find account"], 2500) \
            or page.keyboard.press("Enter")
        page.wait_for_timeout(6000)
        snap(page, "after-identify")

        body = _txt(page).lower()
        if "can't find" in body or "couldn't find" in body or "no account" in body:
            ctx.close()
            return 2

        # "Choose a way to log in" may hide contact methods behind "+N" —
        # expand first, then prefer SMS, then email.
        click_text(page, ["and 5 more", "more", "Show more"], 1500)
        page.wait_for_timeout(1500)
        snap(page, "login-options")
        body2 = _txt(page).lower()
        if "send code" in body2 or "get code" in body2 or "text message" in body2 \
                or "sms" in body2 or "email" in body2:
            click_text(page, ["Send code via SMS", "Text message", "SMS",
                              "Send code via email", "email", "Send code",
                              "Get code", "Continue"], 2500)
            page.wait_for_timeout(5000)
            snap(page, "after-code-request")

        print("[poll] waiting for FB/Meta code (240s)…")
        code = poll_code()
        if not code:
            snap(page, "code-timeout")
            ctx.close()
            return 3

        # enter code
        for sel in ('input[name="code"]', 'input[inputmode="numeric"]',
                    'input[type="tel"]', 'input[type="text"]', 'input'):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible(timeout=2500):
                    loc.click(timeout=2000)
                    loc.press_sequentially(code, delay=30)
                    break
            except Exception:
                continue
        page.wait_for_timeout(400)
        click_text(page, ["Continue", "Next", "Confirm", "Submit"], 2500) \
            or page.keyboard.press("Enter")
        page.wait_for_timeout(6000)
        snap(page, "after-code")

        # new password form (may appear twice: new + confirm)
        pw_fields = page.locator('input[type="password"]')
        n = pw_fields.count()
        print("password fields:", n)
        if n >= 1:
            pw_fields.first.click()
            pw_fields.first.press_sequentially(new_pw, delay=15)
            if n >= 2:
                pw_fields.nth(1).click()
                pw_fields.nth(1).press_sequentially(new_pw, delay=15)
            page.wait_for_timeout(400)
            click_text(page, ["Continue", "Save", "Reset password", "Submit",
                              "Next"], 2500) or page.keyboard.press("Enter")
            page.wait_for_timeout(6000)
            snap(page, "after-pw-set")
            save_env_pw(new_pw)
            ctx.close()
            return 0

        snap(page, "unexpected")
        ctx.close()
        return 4


if __name__ == "__main__":
    sys.exit(main())
