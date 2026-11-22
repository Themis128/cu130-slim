#!/usr/bin/env python3
"""ig_emaillogin_session.py — passwordless IG session via Meta's email login link.

Cold-start lane for the business_login OAuth drive when INSTAGRAM_PASSWORD
is stale/rejected (CAA "Incorrect password" masks account-recovery state —
instagrapi #2881). Meta's "we've made it easy to get back on Instagram"
mail carries ``instagram.com/_n/web_emaillogin?uid=…&token=…`` which logs in
WITHOUT the password, then lands on the 2FA method picker.

Flow (verified live 2026-11-22):
  1. Resolve link: --link URL, or auto-fetch the newest web_emaillogin URL
     from Gmail (baltzakis.themis@gmail.com via gmail-token.json).
  2. Playwright persistent context → open link → "Try another way" →
     "Authentication app" → TOTP from META_TOTP_SECRET_IG.
     NOTE: the enrolled secret is 80-bit base32 — under pyotp's 112-bit
     minimum — so TOTP is computed with stdlib hmac (any length works).
  3. Tick "Trust this device" when offered → poll for sessionid cookie.
  4. --dump PATH writes cookies JSON (0600); --inject pushes them into the
     debug Chrome (:9229) so ig2_oauth_drive.py / ig_bl_session_drive.py
     find a live IG session.

Links are single-use-ish and expire; on failure re-run (a fresh Meta mail
arrives per recovery request — ig_pw_reset.py can trigger one).

Usage:
    ig_emaillogin_session.py                    # auto-fetch link, dump cookies
    ig_emaillogin_session.py --link 'https://…web_emaillogin?…'
    ig_emaillogin_session.py --dump /tmp/ig_cookies.json --inject

Exit codes: 0 ok / 2 no-link / 3 2fa-or-checkpoint-failed /
            4 unexpected-page / 5 infra-error
Never prints cookies, tokens, TOTP secrets, or codes.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import sys
import time

REPO = os.path.expanduser("~/cu130-slim")
sys.path.insert(0, os.path.join(REPO, ".devin/skills/credential-reset-ops/scripts"))
sys.path.insert(0, os.path.join(REPO, ".devin/skills/instagram-ops/scripts"))
from ig_pw_reset import chrome_exe, _gmail_access_token  # noqa: E402
from ig_login import _click_text, _txt, load_env  # noqa: E402

PROFILE = "/tmp/ig-emaillogin-profile"
EMAILLOGIN_RE = re.compile(
    r"https://(?:www\.)?instagram\.com/_n/web_emaillogin[^\s\"'<>]+"
)
# URLs are not full-text indexed — scan Meta recovery mail bodies
GMAIL_Q = "from:noreply@account.meta.com newer_than:2d"


def totp_now(secret: str) -> str:
    """stdlib TOTP — works for secrets shorter than pyotp's 112-bit floor."""
    key = base64.b32decode(re.sub(r"[\s=]", "", secret).upper())
    digest = hmac.new(key, struct.pack(">Q", int(time.time()) // 30),
                      hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", digest[off:off + 4])[0]
                      & 0x7FFFFFFF) % 1000000)


def _b64parts(payload: dict):
    if payload.get("body", {}).get("data"):
        yield payload["body"]["data"]
    for part in payload.get("parts", []) or []:
        yield from _b64parts(part)


def fetch_links() -> list[str]:
    """All web_emaillogin URLs, newest first from Meta recovery mail.

    Gmail format=full body parts are base64url — the URL never appears in
    the raw JSON, so gmail_search()'s regex-on-JSON cannot see it. Decode
    text/html + text/plain parts explicitly (verified 2026-11-22)."""
    import base64
    import urllib.parse
    import urllib.request
    at = _gmail_access_token()
    if not at:
        return []
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages"
            f"?q={urllib.parse.quote(GMAIL_Q)}&maxResults=10",
            headers={"Authorization": "Bearer " + at}))
        msgs = json.loads(r.read()).get("messages", [])
    except Exception as exc:
        print("gmail list err:", exc)
        return []
    links: list[str] = []
    for m in msgs:
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
                f"{m['id']}?format=full",
                headers={"Authorization": "Bearer " + at}))
            msg = json.loads(r.read())
        except Exception:
            continue
        blob = ""
        for d in _b64parts(msg.get("payload", {})):
            try:
                blob += base64.urlsafe_b64decode(d + "==").decode(
                    "utf-8", "replace")
            except Exception:
                pass
        blob = blob.replace("\\u0026", "&").replace("&amp;", "&")
        hit = EMAILLOGIN_RE.search(blob)
        if hit:
            links.append(hit.group(0))
    # dedupe preserving newest-first order
    seen, out = set(), []
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_link() -> str | None:
    links = fetch_links()
    return links[0] if links else None


def logged_in(ctx) -> bool:
    return any(c["name"] == "sessionid"
               for c in ctx.cookies("https://www.instagram.com"))


def handle_2fa(page, ctx, secret: str) -> str:
    """Method picker → Authentication app → TOTP → trust device.
    Returns 'ok' | 'bad-code' | 'stuck'."""
    _click_text(page, ["Try another way", "Try Another Way"], timeout=6000)
    page.wait_for_timeout(2000)
    if not _click_text(page, ["Authentication app", "authentication app"],
                       timeout=5000):
        # picker may already be open with the option visible as a row
        try:
            page.locator("text=Authentication app").first.click(timeout=3000)
        except Exception:
            return "stuck"
    page.wait_for_timeout(1200)
    try:
        cb = page.locator('input[type="checkbox"]').first
        if cb.count() and cb.is_visible(timeout=1500):
            cb.check(timeout=2000)
    except Exception:
        pass
    _click_text(page, ["Continue", "Confirm", "Next"], timeout=4000)
    page.wait_for_timeout(2500)

    rem = 30 - int(time.time()) % 30
    if rem < 5:
        time.sleep(rem + 1)
    code = totp_now(secret)
    filled = False
    for sel in (
        'input[name="verificationCode"]',
        'input[inputmode="numeric"]',
        'input[type="tel"]',
        'input[autocomplete="one-time-code"]',
        'input[type="text"]',
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=2500):
                loc.fill(code)
                filled = True
                break
        except Exception:
            continue
    if not filled:
        boxes = page.locator('input[maxlength="1"]')
        if boxes.count() >= 6:
            for i, ch in enumerate(code):
                boxes.nth(i).fill(ch)
            filled = True
    if not filled:
        return "stuck"
    page.wait_for_timeout(400)
    _click_text(page, ["Continue", "Confirm", "Next", "Submit"], timeout=4000)
    page.wait_for_timeout(1500)
    _click_text(page, ["Continue", "Confirm", "Next", "Submit"], timeout=3000)

    for _ in range(20):
        page.wait_for_timeout(3000)
        if logged_in(ctx):
            return "ok"
        low = (_txt(page) or "")[:300].lower()
        if "incorrect code" in low or "invalid code" in low:
            return "bad-code"
        if "trust this device" in low or "save your login" in low:
            try:
                cb = page.locator('input[type="checkbox"]').first
                if cb.count():
                    cb.check(timeout=1500)
            except Exception:
                pass
            _click_text(page, ["Continue", "Next", "Confirm", "Not now",
                               "Not Now"], timeout=3000)
    return "stuck"


def inject_into_debug_chrome(cookies: list[dict], cdp_url: str) -> None:
    """Push IG cookies into debug Chrome default context via CDP."""
    from playwright.sync_api import sync_playwright
    endpoint = cdp_url.replace("http://", "").rstrip("/")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://{endpoint}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        ctx.add_cookies([
            {**c, "domain": ".instagram.com", "path": "/"}
            for c in cookies
            if c.get("name") in (
                "sessionid", "ds_user_id", "csrftoken", "mid", "ig_did",
                "datr", "ps_l", "ps_n",
            )
        ])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded",
                  timeout=30000)
        page.wait_for_timeout(3000)
        ok = logged_in(ctx)
        print(f"inject verify url={page.url[:80]} logged_in={ok}")
        browser.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--link", help="web_emaillogin URL (else auto-fetch Gmail)")
    ap.add_argument("--dump", metavar="PATH", help="write cookies JSON")
    ap.add_argument("--inject", action="store_true",
                    help="push cookies into debug Chrome (INSTAGRAM_BROWSER_CDP_URL)")
    ap.add_argument("--profile", default=PROFILE)
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    env = load_env()
    secret = env.get("META_TOTP_SECRET_IG") or env.get("META_TOTP_SECRET", "")
    cdp_url = env.get("INSTAGRAM_BROWSER_CDP_URL", "http://192.168.96.1:9229")
    if not secret:
        print("META_TOTP_SECRET_IG/_SECRET missing — 2FA cannot be automated",
              file=sys.stderr)
        return 5

    candidates = [args.link] if args.link else fetch_links()
    if not candidates:
        print("no web_emaillogin link — pass --link or request a fresh "
              "Meta recovery mail", file=sys.stderr)
        return 2
    candidates = [u.replace("&amp;", "&") for u in candidates]

    import ig_pw_reset  # for _pwlibs_env (already imported chrome_exe/gmail_search)
    pwenv = ig_pw_reset._pwlibs_env()
    from playwright.sync_api import sync_playwright

    last_rc = 4
    for link in candidates:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                args.profile,
                executable_path=chrome_exe(),
                headless=args.headless,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                env=pwenv,
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(link, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)

            if not logged_in(ctx):
                url = page.url
                if "one_click_login_error" in url or "invalid_link" in url:
                    print(f"link dead: {url[:90]} — trying next candidate")
                    ctx.close()
                    last_rc = 4
                    continue
                if "two_step_verification" not in url and \
                        "two-factor" not in (_txt(page) or "").lower():
                    print(f"unexpected page: {url[:100]}")
                    print(_txt(page).replace("\n", " | ")[:300])
                    ctx.close()
                    return 4
                rc = handle_2fa(page, ctx, secret)
                if rc != "ok":
                    print(f"2fa lane result: {rc}")
                    print(_txt(page).replace("\n", " | ")[:300])
                    ctx.close()
                    return 3

            cookies = ctx.cookies("https://www.instagram.com")
            names = {c["name"] for c in cookies}
            if "sessionid" not in names:
                print("no sessionid cookie — login incomplete",
                      file=sys.stderr)
                ctx.close()
                return 3
            uid = next((c["value"] for c in cookies
                        if c["name"] == "ds_user_id"), "")
            print(f"OK cookies={len(cookies)} ds_user_id={uid}")
            if args.dump:
                with open(args.dump, "w") as f:
                    json.dump(cookies, f)
                os.chmod(args.dump, 0o600)
                print(f"cookies dumped -> {args.dump}")
            ctx.close()
            break
    else:
        print("all candidate links dead — request a fresh Meta recovery "
              "mail (ig_pw_reset.py) and re-run", file=sys.stderr)
        return last_rc

    if args.inject:
        inject_into_debug_chrome(cookies, cdp_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
