#!/usr/bin/env python3
"""threads_cdp_drive.py — unattended Threads re-auth via debug Chrome CDP.

threads_reauth.py drives the shared browser bridge (browser-novnc:9223),
which needs a live threads.net session. When the bridge is logged out but
the debug Chrome (:9229) holds a live Instagram session, this script does
the whole flow instead:

  1. Mint a fresh /api/v1/auth/oauth/threads/authorize URL via the public
     API (admin creds from .env — SOCIAL_ADMIN_EMAIL/PASSWORD).
  2. Playwright connect_over_cdp → open the URL in debug Chrome.
  3. threads.com/login → click "Log in with Instagram" → instagram.com
     OAuth → "Continue as cloudless.gr" (IG session already live) →
     2FA via META_TOTP_SECRET_IG when challenged → callback to
     social.cloudless.gr.
  4. Verify social_accounts rows flipped to active+fresh via a throwaway
     asyncpg query inside social-api.

Usage:
    threads_cdp_drive.py                 # full drive
    threads_cdp_drive.py --mint-only     # print authorize URL, no browser
    threads_cdp_drive.py --cdp http://host:9229 --timeout 240

Env: SOCIAL_API (default https://social.cloudless.gr), THREADS_BROWSER_CDP_URL
(default http://192.168.96.1:9229). Requires playwright (venv-igreset).
Never prints tokens, cookies, TOTP secrets, or codes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse

REPO = os.path.expanduser("~/cu130-slim")
ENV_FILE = os.path.join(REPO, ".env")
CALLBACK_MARK = "/api/v1/auth/oauth/threads/callback"


def _host_is(url: str, domain: str) -> bool:
    """Hostname-suffix match — 'instagram.com' in url is spoofable
    (evil.com/?q=instagram.com); CodeQL py/incomplete-url-substring-sanitization."""
    host = (urlparse(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


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


def _req(url: str, data: dict | None = None, token: str | None = None,
         form: bool = False, timeout: int = 30):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(r, timeout=timeout) as f:
        raw = f.read()
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {"_raw": raw[:300].decode("utf-8", "replace")}


def admin_token(api: str, env: dict) -> str:
    return _req(
        f"{api}/api/v1/auth/login",
        data={"username": env["SOCIAL_ADMIN_EMAIL"],
              "password": env["SOCIAL_ADMIN_PASSWORD"]},
        form=True, timeout=20,
    )["access_token"]


def mint_authorize_url(api: str, env: dict) -> str:
    tok = admin_token(api, env)
    teams = _req(f"{api}/api/v1/teams", token=tok)
    items = teams if isinstance(teams, list) else teams.get("items", [])
    team_id = items[0].get("id")
    if not team_id:
        raise RuntimeError(f"no team id in {teams!r:.200}")
    auth = _req(f"{api}/api/v1/auth/oauth/threads/authorize?team_id={team_id}",
                token=tok)
    url = auth.get("authorization_url")
    if not url:
        raise RuntimeError(f"no authorization_url: {auth!r:.300}")
    return url


def totp_now(secret: str) -> str:
    """stdlib TOTP — works for secrets under pyotp's 112-bit floor."""
    import base64
    import hashlib
    import hmac
    import struct
    key = base64.b32decode(re.sub(r"[\s=]", "", secret).upper())
    digest = hmac.new(key, struct.pack(">Q", int(time.time()) // 30),
                      hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", digest[off:off + 4])[0]
                      & 0x7FFFFFFF) % 1000000)


# ---- Playwright drive -------------------------------------------------

_CLICK_JS = """
(() => {
  const els = [...document.querySelectorAll(
      'button,[role=button],a[role=link],a,div[tabindex],span[role=button]')];
  const vis = e => { const r = e.getBoundingClientRect();
                     return r.width > 0 && r.height > 0; };
  const cand = els.filter(vis);
  const txt = e => (e.innerText || e.textContent ||
                    e.getAttribute('aria-label') || "").trim();
  const deny = /cancel|decline|not now|maybe later|deny|log out|switch/i;
  // prefer the most specific clickable: real links/buttons first, then
  // shortest matching text (avoids clicking wrapper DIVs)
  const rank = e => (e.tagName === 'A' || e.getAttribute('role') === 'button'
                     ? 0 : 1) * 1000 + txt(e).length;
  const pick = (re, max=80) => cand
      .filter(e => re.test(txt(e)) && txt(e).length < max
               && !deny.test(txt(e)))
      .sort((a, b) => rank(a) - rank(b))[0];
  const b = pick(/use your instagram account|log in with instagram|continue as cloudless|cloudless\\.gr/i)
         || pick(/continue|allow|authorize|approve|next|confirm|got it/i, 60);
  if (b) { b.click(); return 'clicked:' + txt(b).slice(0, 40); }
  return 'no-button';
})()
"""


def _txt(page) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:
        return ""


def _fill_first(page, selectors, value):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=1500):
                loc.fill(value)
                return True
        except Exception:
            continue
    return False


def drive(url: str, cdp: str, timeout: int, secret: str) -> int:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        deadline = time.time() + timeout
        last_log = 0
        while time.time() < deadline:
            cur = page.url
            pu = urlparse(cur)
            is_cb = pu.netloc.endswith("cloudless.gr") and "callback" in pu.path
            if is_cb:
                print(f"callback reached: {cur[:120]}")
                page.wait_for_timeout(5000)
                browser.close()
                return 0
            low = _txt(page).lower()
            body_head = _txt(page)[:200].replace("\n", " | ")

            # threads.com login wall → "Log in with Instagram"
            if "threads.com/login" in cur or "threads.net/login" in cur:
                low0 = _txt(page).lower()
                # chooser variant → real link click; login variant → IG button
                for sel in ("a:has-text('Use your Instagram account')",
                            "a:has-text('Instagram')",
                            "button:has-text('Continue with Instagram')",
                            "button:has-text('Log in with Instagram')"):
                    try:
                        loc = page.locator(sel).first
                        if loc.count():
                            loc.click(timeout=2500)
                            break
                    except Exception:
                        continue
                else:
                    page.evaluate(_CLICK_JS)
            # IG OAuth consent / account chooser
            elif _host_is(cur, "instagram.com"):
                if "two-factor" in low or "authentication app" in low or \
                        "verification code" in low:
                    print("2FA wall — driving TOTP")
                    _handle_ig_2fa(page, secret)
                else:
                    page.evaluate(_CLICK_JS)
            # generic consent
            else:
                page.evaluate(_CLICK_JS)

            if time.time() - last_log > 15:
                print(f"  …{cur[:100]}\n    {body_head[:140]}")
                last_log = time.time()
            page.wait_for_timeout(3000)

        print(f"TIMEOUT at {page.url[:120]}")
        print(_txt(page)[:300].replace("\n", " | "))
        browser.close()
        return 4


def _handle_ig_2fa(page, secret: str) -> None:
    try:
        page.locator("text=Authentication app").first.click(timeout=3000)
    except Exception:
        try:
            page.locator("text=Try another way").first.click(timeout=2000)
            page.wait_for_timeout(1500)
            page.locator("text=Authentication app").first.click(timeout=3000)
        except Exception:
            pass
    page.wait_for_timeout(1200)
    try:
        cb = page.locator('input[type="checkbox"]').first
        if cb.count() and cb.is_visible(timeout=1200):
            cb.check(timeout=1500)
    except Exception:
        pass
    for label in ("Continue", "Confirm", "Next"):
        try:
            page.locator(f"button:has-text('{label}')").first.click(
                timeout=1200)
            break
        except Exception:
            continue
    rem = 30 - int(time.time()) % 30
    if rem < 5:
        time.sleep(rem + 1)
    code = totp_now(secret)
    if not _fill_first(page, [
        'input[name="verificationCode"]',
        'input[inputmode="numeric"]',
        'input[type="tel"]',
        'input[type="text"]',
    ], code):
        boxes = page.locator('input[maxlength="1"]')
        if boxes.count() >= 6:
            for i, ch in enumerate(code):
                boxes.nth(i).fill(ch)
    page.wait_for_timeout(400)
    for label in ("Continue", "Confirm", "Next", "Submit"):
        try:
            page.locator(f"button:has-text('{label}')").first.click(
                timeout=1500)
            break
        except Exception:
            continue


def verify_rows() -> int:
    """Check threads rows via asyncpg inside social-api."""
    sql = (
        "SELECT username,status,"
        "COALESCE(token_expires_at > now() + interval '1 hour',false) AS fresh "
        "FROM social_accounts WHERE platform='threads'"
    )
    code = (
        "import asyncio,asyncpg,os,json\n"
        "async def go():\n"
        " c=await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://'))\n"
        " r=await c.fetch(\"" + sql.replace('"', '\\"') + "\")\n"
        " print(json.dumps([dict(x) for x in r],default=str))\n"
        " await c.close()\n"
        "asyncio.run(go())"
    )
    try:
        out = subprocess.run(
            ["docker", "exec", "social-api", "python3", "-c", code],
            capture_output=True, text=True, timeout=30)
        print("rows:", out.stdout.strip()[:600] or out.stderr.strip()[:300])
        return 0
    except Exception as e:
        print(f"verify failed: {e}")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mint-only", action="store_true")
    ap.add_argument("--cdp", default=None,
                    help="CDP endpoint (default env/192.168.96.1:9229)")
    ap.add_argument("--api", default=None,
                    help="SocialAuto base URL (default env/https://social.cloudless.gr)")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    env = load_env()
    api = args.api or env.get("SOCIAL_API_PUBLIC", "http://127.0.0.1:8083")
    cdp = args.cdp or env.get("THREADS_BROWSER_CDP_URL",
                              "http://192.168.96.1:9229")
    secret = env.get("META_TOTP_SECRET_IG") or env.get("META_TOTP_SECRET", "")

    url = mint_authorize_url(api, env)
    print(f"authorize URL minted ({url[:80]}…)")
    if args.mint_only:
        print(url)
        return 0

    rc = drive(url, cdp, args.timeout, secret)
    if rc == 0:
        time.sleep(8)  # let the backend exchange the code
        verify_rows()
    return rc


if __name__ == "__main__":
    sys.exit(main())
