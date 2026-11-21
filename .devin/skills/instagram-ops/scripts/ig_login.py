#!/usr/bin/env python3
"""ig_login.py — unattended Instagram re-login (password + TOTP) via Playwright.

Heals the IG session with zero human steps when the browser session dies:

  1. Fresh Playwright chromium context (no profile) → instagram.com login.
  2. Username/password from .env (INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD).
  3. 2FA: "Try another way" → "Authentication app" → code from
     META_TOTP_SECRET_IG (enrolled 2026-11-21, backlog entry).
  4. On success: exports session cookies and --inject pushes them into the
     debug Chrome (:9229, INSTAGRAM_BROWSER_CDP_URL) so the CDP publish lane
     is logged in again — or --dump writes them to a JSON file for transplant.

The aiograpi/private-API lane is NOT used: upstream ClientNeedsUpgradeError
(instagrapi #2881) fires before credential checks.

Usage:
    ig_login.py                  # headed login, report only
    ig_login.py --headless       # headless attempt (higher checkpoint risk)
    ig_login.py --inject         # + push cookies into debug Chrome :9229
    ig_login.py --dump /tmp/ig_cookies.json

Exit codes: 0 ok / 2 bad-credentials / 3 checkpoint-or-2fa-failed /
            4 unexpected-page / 5 infra-error
Never prints passwords, TOTP secrets, or session cookie values.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

REPO = os.path.expanduser("~/cu130-slim")
ENV_FILE = os.path.join(REPO, ".env")
IG_LOGIN_URL = "https://www.instagram.com/accounts/login/"


def load_env() -> dict[str, str]:
    env = {}
    try:
        for line in open(ENV_FILE):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError as exc:
        print(f"env read failed: {exc}", file=sys.stderr)
        sys.exit(5)
    return env


def totp_now(secret: str) -> str:
    try:
        import pyotp
        t = pyotp.TOTP(secret)
        rem = 30 - int(time.time()) % 30
        if rem < 5:
            time.sleep(rem + 1)
        return t.now()
    except ImportError:
        import base64, hashlib, hmac, struct
        counter = int(time.time() // 30)
        key = base64.b32decode(secret.upper())
        hs = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        off = hs[-1] & 0x0F
        return "%06d" % ((int.from_bytes(hs[off:off + 4], "big") & 0x7FFFFFFF) % 10**6)


def _txt(page) -> str:
    try:
        return (page.inner_text("body") or "")[:800]
    except Exception:
        return ""


def _click_text(page, texts: list[str], timeout: int = 4000) -> bool:
    """Click the first visible button/link/label/div[role=button] matching text."""
    for text in texts:
        for sel in (
            f"button:has-text('{text}')",
            f"div[role='button']:has-text('{text}')",
            f"a:has-text('{text}')",
            f"label:has-text('{text}')",
            f"[role='button']:has-text('{text}')",
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible(timeout=timeout):
                    try:
                        loc.click(timeout=timeout)
                    except Exception:
                        # Meta/IG overlays intercept pointer events — fall back
                        # to a DOM click which still fires React handlers.
                        loc.dispatch_event("click")
                    return True
            except Exception:
                continue
    return False


def snap(page, tag: str) -> None:
    print(f"--- {tag} url={page.url}")
    print(_txt(page).replace("\n", " | ")[:400])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--inject", action="store_true", help="push cookies into debug Chrome")
    ap.add_argument("--dump", metavar="PATH", help="write cookies JSON to PATH")
    ap.add_argument("--profile", default="/tmp/ig-login-profile", help="persistent profile dir")
    args = ap.parse_args()

    env = load_env()
    user = env.get("INSTAGRAM_USERNAME", "")
    pw = env.get("INSTAGRAM_PASSWORD", "")
    totp_secret = env.get("META_TOTP_SECRET_IG") or env.get("META_TOTP_SECRET", "")
    cdp_url = env.get("INSTAGRAM_BROWSER_CDP_URL", "http://192.168.96.1:9229")
    if not user or not pw:
        print("INSTAGRAM_USERNAME/PASSWORD missing in .env", file=sys.stderr)
        return 5
    if not totp_secret:
        print("META_TOTP_SECRET_IG/_SECRET missing — 2FA cannot be automated", file=sys.stderr)
        return 5

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # venv playwright may lag the ms-playwright cache — pin the newest
        # installed chromium binary if the default revision is missing.
        exe = None
        cache = os.path.expanduser("~/.cache/ms-playwright")
        if os.path.isdir(cache):
            revs = sorted(
                (d for d in os.listdir(cache) if d.startswith("chromium-")),
                key=lambda d: int(d.rsplit("-", 1)[1]),
            )
            for rev in reversed(revs):
                cand = os.path.join(cache, rev, "chrome-linux64", "chrome")
                if os.path.exists(cand):
                    exe = cand
                    break
        ctx = p.chromium.launch_persistent_context(
            args.profile,
            executable_path=exe,
            headless=args.headless,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(IG_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        # Already logged in? (fresh profile should not be, but cheap check)
        if "instagram.com" in page.url and "/accounts/login" not in page.url:
            print("already-authenticated (unexpected on fresh profile)")
            cookies = ctx.cookies("https://www.instagram.com")
            return finish(cookies, args, cdp_url, 0)

        # Credentials — IG rotates input names; try known selectors in order.
        user_sel = None
        for sel in (
            'input[name="username"]',
            'input[name="email"]',
            'input[autocomplete="username"]',
            'input[type="text"]',
            'input[type="tel"]',
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible(timeout=3000):
                    user_sel = loc
                    break
            except Exception:
                continue
        pw_sel = None
        for sel in ('input[name="password"]', 'input[type="password"]'):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible(timeout=3000):
                    pw_sel = loc
                    break
            except Exception:
                continue
        if user_sel is None or pw_sel is None:
            snap(page, "login-form-missing")
            print("login inputs not found", file=sys.stderr)
            ctx.close()
            return 4
        user_sel.fill(user)
        pw_sel.fill(pw)
        _click_text(page, ["Log in", "Log In"], timeout=3000) or page.keyboard.press("Enter")
        page.wait_for_timeout(4000)

        body = _txt(page)
        url = page.url

        # Wrong password surface
        if "incorrect" in body.lower() or "wrong password" in body.lower() or "couldn't find" in body.lower():
            snap(page, "bad-credentials")
            ctx.close()
            return 2

        # 2FA challenge
        if "two-factor" in body.lower() or "authentication app" in body.lower() or "verification code" in body.lower():
            print("2fa challenge detected")
            # Expand alternate methods if needed
            _click_text(page, ["Try another way", "Try Another Way"], timeout=3000)
            page.wait_for_timeout(1500)
            ok = _click_text(page, ["Authentication app", "authentication app"], timeout=4000)
            if not ok:
                snap(page, "2fa-no-authapp-option")
                ctx.close()
                return 3
            page.wait_for_timeout(1500)
            code_loc = page.locator('input[name="verificationCode"], input[inputmode="numeric"], input[type="tel"], input[autocomplete="one-time-code"]').first
            try:
                code_loc.fill(totp_now(totp_secret), timeout=8000)
            except Exception:
                # some flows show 6 separate boxes
                boxes = page.locator('input[maxlength="1"]')
                if boxes.count() >= 6:
                    code = totp_now(totp_secret)
                    for i, ch in enumerate(code):
                        boxes.nth(i).fill(ch)
                else:
                    snap(page, "2fa-code-input-missing")
                    ctx.close()
                    return 3
            _click_text(page, ["Continue", "Confirm", "Next"], timeout=3000) or page.keyboard.press("Enter")
            page.wait_for_timeout(5000)
            body = _txt(page)
            if "incorrect code" in body.lower() or "invalid" in body.lower():
                snap(page, "2fa-bad-code")
                ctx.close()
                return 3

        # Interstitials: save-login / notifications / was-this-you
        for _ in range(4):
            body = _txt(page)
            low = body.lower()
            if "save your login" in low or "save login info" in low:
                _click_text(page, ["Not now", "Not Now"], timeout=3000)
            elif "turn on notifications" in low or "enable notifications" in low:
                _click_text(page, ["Not Now", "Not now", "No thanks"], timeout=3000)
            elif "was this you" in low or "confirm your identity" in low:
                _click_text(page, ["This was me", "It was me", "Yes, this was me"], timeout=3000)
            elif "suspicious" in low or "unusual activity" in low or "checkpoint" in low or "verify" in low and "email" in low:
                snap(page, "checkpoint-wall")
                ctx.close()
                return 3
            else:
                break
            page.wait_for_timeout(2500)

        # Success check: feed / home, not login/challenge
        final_url = page.url
        if "/accounts/login" in final_url or "challenge" in final_url:
            snap(page, "still-on-login")
            ctx.close()
            return 3
        if "instagram.com" not in final_url:
            snap(page, "unexpected-redirect")
            ctx.close()
            return 4

        snap(page, "post-login")
        cookies = ctx.cookies("https://www.instagram.com")
        rc = finish(cookies, args, cdp_url, 0)
        ctx.close()
        return rc


def finish(cookies: list[dict], args, cdp_url: str, rc: int) -> int:
    names = {c["name"] for c in cookies}
    uid = next((c["value"] for c in cookies if c["name"] == "ds_user_id"), "")
    print(f"cookies={len(cookies)} sessionid={'yes' if 'sessionid' in names else 'NO'} ds_user_id={uid}")
    if "sessionid" not in names:
        print("no sessionid cookie — login did not complete", file=sys.stderr)
        return 3
    if args.dump:
        with open(args.dump, "w") as f:
            json.dump(cookies, f)
        os.chmod(args.dump, 0o600)
        print(f"cookies dumped -> {args.dump}")
    if args.inject:
        inject_into_debug_chrome(cookies, cdp_url)
    return rc


def inject_into_debug_chrome(cookies: list[dict], cdp_url: str) -> None:
    """Push IG cookies into the debug Chrome default context via CDP."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright unavailable for inject", file=sys.stderr)
        return
    endpoint = cdp_url.replace("http://", "").rstrip("/")
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://{endpoint}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            ctx.add_cookies([
                {**c, "domain": ".instagram.com", "path": "/"}
                for c in cookies
                if c.get("name") in ("sessionid", "ds_user_id", "csrftoken", "mid", "ig_did", "datr", "ps_l", "ps_n")
            ])
            # verify: open/feed check
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            body = _txt(page)
            ok = "log in" not in body.lower()[:200] and ("instagram" in page.url)
            print(f"inject verify url={page.url} looks_logged_in={ok}")
            browser.close()
    except Exception as exc:
        print(f"inject failed: {type(exc).__name__}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
