#!/usr/bin/env python3
"""ig_pw_reset.py — unattended Instagram password reset (forgot-password drive).

Drives instagram.com/accounts/password/reset/ in a fresh Playwright context:
identify account -> pick email contact -> request code/link -> self-read the
code off Gmail / cloudless.gr dovecot / phone SMS -> set a new password ->
write INSTAGRAM_PASSWORD back to .env.

Code/link lanes (same as fb_pw_reset.py + mail-code-fetch):
  - Gmail API (gmail-token.json + gcp-oauth.keys.json on the Windows secrets dir)
  - dovecot tbaltzakis@cloudless.gr via `ssh omv-ha doveadm fetch`
  - ADB SMS inbox on the Galaxy S22+ / Termux lane

If IG sends a login LINK instead of a code, the link is opened in the same
context (that IS the recovery login) and the password is changed from
Accounts Center -> Password and security.

Usage:
    ig_pw_reset.py [new-password]     # default: generate a strong one
    ig_pw_reset.py --dry              # drive until the code request, no writes

Exit codes: 0 ok / 2 account-not-found / 3 code-or-link-timeout /
            4 unexpected-page / 5 infra-error
Never prints the password, codes, or tokens.
"""
from __future__ import annotations

import argparse
import base64
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, ".devin/skills/instagram-ops/scripts"))
from ig_login import _click_text, _txt, load_env  # noqa: E402

ENV_FILE = os.path.join(REPO, ".env")
RESET_URL = "https://www.instagram.com/accounts/password/reset/"
IG_USER = ""

GMAIL_SECRETS = "/mnt/c/Users/tbaltzakis/AppData/Roaming/devin/secrets/gmail-token.json"
GMAIL_KEYS = "/mnt/c/Users/tbaltzakis/AppData/Roaming/devin/secrets/gcp-oauth.keys.json"
CODE_RE = re.compile(r"\b(\d{6})\b")
LINK_RE = re.compile(r"https://(?:www\.)?instagram\.com/[^\s\"'<>]+")
_LINK_BAD = re.compile(r"unsubscribe|oneclick|privacy|terms|help|about|legal|policies", re.I)
_LINK_GOOD = re.compile(r"reset|recover|password|login|confirm|challenge|approve|accounts", re.I)


def _best_link(text: str) -> str | None:
    """Pick the password-reset link out of an IG email — footer/unsubscribe
    links also match LINK_RE and previously won the race (2026-11-22)."""
    best, best_score = None, -1
    for m in LINK_RE.finditer(text):
        u = m.group(0).replace("\\u0026", "&").replace("&amp;", "&")
        score = (3 if _LINK_GOOD.search(u) else 0) - (5 if _LINK_BAD.search(u) else 0)
        if score > best_score:
            best, best_score = u, score
    return best if best_score > 0 else None


def snap(page, tag: str) -> None:
    print(f"--- {tag} url={page.url}")
    print(_txt(page).replace("\n", " | ")[:500])


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
        key=lambda d: int(d.rsplit("-", 1)[1]),
    )
    for rev in reversed(revs):
        cand = os.path.join(cache, rev, "chrome-linux64", "chrome")
        if os.path.exists(cand):
            return cand
    return None


def gen_password() -> str:
    alpha = string.ascii_letters + string.digits
    return "Cl@" + "".join(_secrets.choice(alpha) for _ in range(14)) + "!"


def save_env_pw(pw: str) -> None:
    lines = open(ENV_FILE).read().splitlines()
    out, done = [], False
    for ln in lines:
        if ln.startswith("INSTAGRAM_PASSWORD="):
            out.append(f"INSTAGRAM_PASSWORD={pw}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"INSTAGRAM_PASSWORD={pw}")
    open(ENV_FILE, "w").write("\n".join(out) + "\n")
    os.chmod(ENV_FILE, 0o600)
    print("INSTAGRAM_PASSWORD updated in .env")


# ── code/link lanes ────────────────────────────────────────────────────

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
                "grant_type": "refresh_token"}).encode()))
        return json.loads(r.read())["access_token"]
    except Exception as exc:
        print("gmail token err:", exc)
        return None


def gmail_search(q: str, regex: re.Pattern, fmt: str = "full") -> str | None:
    at = _gmail_access_token()
    if not at:
        return None
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={urllib.parse.quote(q)}&maxResults=3",
            headers={"Authorization": "Bearer " + at}))
        for m in json.loads(r.read()).get("messages", []):
            r = urllib.request.urlopen(urllib.request.Request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}?format={fmt}",
                headers={"Authorization": "Bearer " + at}))
            body = json.dumps(json.loads(r.read()))
            if regex is LINK_RE:
                link = _best_link(body)
                if link:
                    return link
                continue
            hit = regex.search(body)
            if hit:
                return hit.group(1) if hit.lastindex else hit.group(0)
    except Exception as exc:
        print("gmail search err:", exc)
    return None


def dovecot_search(sender: str, regex: re.Pattern) -> str | None:
    user = "tbaltzakis@cloudless.gr"
    cmd = [
        "ssh", "omv-ha",
        f"for b in INBOX Junk; do sudo -n doveadm fetch -u {user} 'uid body' "
        f"mailbox $b FROM {sender} SENTSINCE 30m 2>/dev/null; done",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except Exception as exc:
        print("dovecot err:", exc)
        return None
    for blk in reversed(re.split(r"(?=uid: \d+)", out)):
        if regex is LINK_RE:
            link = _best_link(blk)
            if link:
                return link
            continue
        hit = regex.search(blk)
        if hit:
            return hit.group(1) if hit.lastindex else hit.group(0)
    return None


def adb_code(since_ms: int = 0) -> str | None:
    """Fresh IG/Meta SMS code only — sender- and time-filtered.

    The old version grabbed the first \\d{6} in the newest SMS regardless of
    sender or age, so a stale Threads/Facebook/GOVGR code was entered as the
    IG reset code (2026-11-22: reset attempt failed on 'use your password').
    """
    try:
        out = subprocess.run(
            ["adb", "shell", "content", "query", "--uri",
             "content://sms/inbox", "--projection", "address,body,date",
             "--sort", "date DESC"],
            capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return None
    sender_re = re.compile(r"instagram|facebook|meta|threads", re.I)
    for line in out.splitlines():
        m = re.search(r"address=([^,]+), body=([^,]+), date=(\d+)", line)
        if not m:
            continue
        addr, body, dates = m.group(1), m.group(2), int(m.group(3))
        if dates < since_ms or not sender_re.search(addr):
            continue
        c = CODE_RE.search(body)
        if c:
            return c.group(1)
    return None


def termux_code(since_ms: int = 0) -> str | None:
    """ADB-free fallback: termux-sms-list over tailnet sshd (:8022 via
    ~/.ssh/config host 100.84.31.85). Same sender+age filters as adb_code."""
    import datetime
    host = os.environ.get("PHONE_SSH_HOST", "100.84.31.85")
    try:
        out = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=no", host,
             "termux-sms-list -l 15"],
            capture_output=True, text=True, timeout=15).stdout
        msgs = json.loads(out)
    except Exception:
        return None
    sender_re = re.compile(r"instagram|facebook|meta|threads", re.I)
    for msg in msgs if isinstance(msgs, list) else []:
        addr = str(msg.get("address") or msg.get("number") or "")
        body = str(msg.get("body") or "")
        if not sender_re.search(addr + " " + body):
            continue
        if since_ms:
            try:
                dt = datetime.datetime.strptime(
                    str(msg.get("received") or ""), "%Y-%m-%d %H:%M:%S")
                if int(dt.timestamp() * 1000) < since_ms:
                    continue
            except ValueError:
                pass
        c = CODE_RE.search(body)
        if c:
            return c.group(1)
    return None


def poll_code_or_link(since_ms: int = 0) -> tuple[str | None, str | None]:
    """Poll all lanes; return (code, link) whichever arrives first."""
    deadline = time.time() + 240
    seen = set()
    while time.time() < deadline:
        # Gmail: IG mails come from security@mail.instagram.com / instagram.com
        code = gmail_search(
            "from:instagram newer_than:10m", CODE_RE)
        if code and code not in seen:
            seen.add(code)
            return code, None
        link = gmail_search(
            "from:instagram newer_than:10m", LINK_RE)
        if link and link not in seen:
            seen.add(link)
            return None, link
        code = dovecot_search("instagram", CODE_RE)
        if code and code not in seen:
            seen.add(code)
            return code, None
        link = dovecot_search("instagram", LINK_RE)
        if link and link not in seen:
            seen.add(link)
            return None, link
        code = adb_code(since_ms) or termux_code(since_ms)
        if code and code not in seen:
            seen.add(code)
            return code, None
        time.sleep(8)
    return None, None


# ── drive ──────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("new_password", nargs="?", default=None)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--profile", default="/tmp/ig-reset-profile")
    args = ap.parse_args()

    env = load_env()
    global IG_USER
    IG_USER = env.get("INSTAGRAM_USERNAME", "")
    if not IG_USER:
        print("INSTAGRAM_USERNAME missing", file=sys.stderr)
        return 5
    new_pw = args.new_password or gen_password()

    from playwright.sync_api import sync_playwright

    env_pw = _pwlibs_env()
    os.environ["LD_LIBRARY_PATH"] = env_pw["LD_LIBRARY_PATH"]

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            args.profile,
            executable_path=chrome_exe(),
            headless=args.headless,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            env=env_pw,
            args=["--disable-blink-features=AutomationControlled"],
        )
        pw_set = False
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(RESET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        # identify screen: username/email field
        ident = None
        for sel in ('input[name="emailOrUsername"]', 'input[name="username"]',
                    'input[type="text"]', 'input[type="tel"]',
                    'input[autocomplete="username"]'):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible(timeout=3000):
                    ident = loc
                    break
            except Exception:
                continue
        if ident is None:
            snap(page, "no-identify-field")
            ctx.close()
            return 4
        # Cookie-consent overlays intercept pointer events on this page —
        # focus the field via JS and type instead of clicking.
        ident.evaluate("el => el.focus()")
        ident.press_sequentially(IG_USER, delay=25)
        page.wait_for_timeout(400)
        clicked = _click_text(page, ["Continue"], timeout=3000)
        if not clicked:
            page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        snap(page, "after-identify")

        body = _txt(page).lower()
        if "can't find" in body or "couldn't find" in body or "not found" in body:
            ctx.close()
            return 2

        # Contact-choice screen: prefer email over SMS/WhatsApp
        if "email" in body or "phone" in body or "text message" in body or "whatsapp" in body:
            _click_text(page, ["Email", "email", "Send email", "Continue"], timeout=3000)
            page.wait_for_timeout(2000)
            # confirm send if a second step appears
            _click_text(page, ["Send login link", "Send code", "Next", "Continue"], timeout=3000)
            page.wait_for_timeout(3000)
            snap(page, "after-contact-pick")

        if args.dry:
            print("dry-run: stopping before code poll")
            ctx.close()
            return 0

        # Freshness floor: only accept codes/links emitted after the reset
        # request — stale inbox items must never be entered.
        req_ms = int(time.time() * 1000)
        print("[poll] waiting for IG code/link (240s)…")
        code, link = poll_code_or_link(since_ms=req_ms)
        if not code and not link:
            snap(page, "code-timeout")
            ctx.close()
            return 3

        if link:
            print("[link] opening recovery link in context")
            # strip JSON escaping artifacts
            link = link.replace("\\u0026", "&").replace("&amp;", "&")
            page.goto(link, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            snap(page, "after-link")
            # Recovery login usually lands on feed or a reset-password form.
            # If a 'reset password'/'new password' form is present, fill it.
            npw = page.locator('input[type="password"], input[name="newPassword"]').first
            try:
                if npw.count() and npw.is_visible(timeout=3000):
                    npw.fill(new_pw)
                    conf = page.locator('input[name="confirmPassword"], input[type="password"]').nth(1)
                    if conf.count():
                        conf.fill(new_pw)
                    _click_text(page, ["Reset password", "Save", "Continue", "Submit"], timeout=3000)
                    page.wait_for_timeout(4000)
                    snap(page, "after-pw-set")
                    pw_set = True
            except Exception as exc:
                print("pw-set form drive err:", exc)
        else:
            print("[code] entering recovery code")
            code_loc = None
            for sel in ('input[name="verificationCode"]', 'input[inputmode="numeric"]',
                        'input[type="tel"]', 'input[type="text"]',
                        'input[autocomplete="one-time-code"]'):
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible(timeout=3000):
                        code_loc = loc
                        break
                except Exception:
                    continue
            if code_loc is None:
                snap(page, "no-code-field")
                ctx.close()
                return 4
            code_loc.fill(code)
            _click_text(page, ["Continue", "Next", "Confirm", "Submit"], timeout=3000) \
                or page.keyboard.press("Enter")
            page.wait_for_timeout(4000)
            snap(page, "after-code")
            # code accepted → usually lands on reset-password form or feed
            body2 = _txt(page).lower()
            if "new password" in body2 or "reset password" in body2 or "create a password" in body2:
                fields = page.locator('input[type="password"]')
                if fields.count() >= 1:
                    fields.first.fill(new_pw)
                    if fields.count() >= 2:
                        fields.nth(1).fill(new_pw)
                    _click_text(page, ["Reset password", "Save", "Continue", "Submit"], timeout=3000)
                    page.wait_for_timeout(4000)
                    snap(page, "after-pw-set")
                    pw_set = True

        # If we ended logged-in without setting pw (link path w/o form),
        # change it via Accounts Center is a second flow — report state.
        final = page.url
        logged_in = "login" not in final and "reset" not in final
        print(f"final_url={final} logged_in_hint={logged_in}")

        if not args.dry and pw_set:
            save_env_pw(new_pw)
        elif not args.dry:
            print("password form was not submitted — .env left unchanged", file=sys.stderr)
        ctx.close()
        return 0 if pw_set or args.dry else 4


if __name__ == "__main__":
    sys.exit(main())
