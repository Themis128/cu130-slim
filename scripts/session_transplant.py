#!/usr/bin/env python3
"""Transplant a logged-in browser session between SocialAuto browsers.

Sources:
  --sidecar PORT          read cookies from a sidecar's GET /debug/all-cookies
                          (facebook 9226, linkedin 9225, tiktok 9224)
  --cookies FILE          Playwright-format cookie list JSON (e.g. exported via
                          the Playwright MCP `browser_run_code_unsafe` tool with
                          `page.context().cookies('https://<site>')`)

Target is always the shared browser-novnc bridge (default :9223):
  1. POST /session/start {platform}          (reuses a same-platform session)
  2. POST /session/cookies                  (injects into the live context)
  3. POST /session/navigate {site url}      (verifies login — login pages
                                             redirect or render a login form)
  4. POST /session/extract                  (persists cookies to /data files)

Busy-holds: every request is tagged `X-Platform: <platform>` so calls pass
while OUR platform owns the hold; foreign owners get retried up to ~4 min
(the hold outlives the owner's last call by 180s).

Usage:
  python3 scripts/session_transplant.py --platform facebook --sidecar 9226
  python3 scripts/session_transplant.py --platform instagram --cookies ig.json
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BRIDGE = "http://localhost:9223"
SIDECAR_COOKIE_PATH = "/debug/all-cookies"
NAV_URLS = {
    "facebook": "https://www.facebook.com/",
    "instagram": "https://www.instagram.com/",
    "threads": "https://www.threads.net/",
    "twitter": "https://x.com/",
    "tiktok": "https://www.tiktok.com/",
    "linkedin": "https://www.linkedin.com/feed/",
}
# Markers that prove we are NOT logged in after navigation
LOGIN_MARKERS = {
    "facebook": 'document.querySelector("input[name=email]")',
    "instagram": 'document.querySelector("input[name=username]")',
}


def _call(url, body=None, plat=None, method=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method or ("POST" if body is not None else "GET"),
        headers={
            "Content-Type": "application/json",
            **({"X-Platform": plat} if plat else {}),
        },
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout), None
    except urllib.error.HTTPError as e:
        return None, e


def post(url, body=None, plat=None, timeout=90):
    r, e = _call(url, body, plat, timeout=timeout)
    if e:
        return {"http_error": e.code, "body": e.read()[:300].decode(errors="replace")}
    return json.loads(r.read())


def get(url, timeout=30):
    r, e = _call(url, timeout=timeout)
    if e:
        return {"http_error": e.code, "body": e.read()[:300].decode(errors="replace")}
    return json.loads(r.read())


def sidecar_cookies(port: int) -> list[dict]:
    """Fetch cookies from a sidecar's /debug/all-cookies (returns name->value)."""
    r = get(f"http://localhost:{port}{SIDECAR_COOKIE_PATH}", timeout=60)
    if "http_error" in r:
        sys.exit(f"sidecar :{port} error: {r}")
    cookies = r.get("cookies", {})
    if isinstance(cookies, dict):
        cookies = [{"name": k, "value": v} for k, v in cookies.items()]
    return cookies


def normalize(cookies: list[dict], platform: str) -> list[dict]:
    """Ensure every cookie has Playwright fields (domain/path/secure/sameSite)."""
    default_domain = {
        "facebook": ".facebook.com",
        "instagram": ".instagram.com",
        "threads": ".threads.net",
        "twitter": ".x.com",
        "tiktok": ".tiktok.com",
        "linkedin": ".linkedin.com",
    }.get(platform, "")
    out = []
    for c in cookies:
        out.append(
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain") or default_domain,
                "path": c.get("path") or "/",
                "secure": c.get("secure", True),
                "sameSite": c.get("sameSite") or "Lax",
                **({"expires": c["expires"]} if c.get("expires") else {}),
                **({"httpOnly": c["httpOnly"]} if "httpOnly" in c else {}),
            }
        )
    return out


def wait_window(platform: str, tries: int = 20, sleep_s: int = 12):
    """Poll /session/start until our platform wins the busy-hold."""
    for _ in range(tries):
        r = post(f"{BRIDGE}/session/start", {"platform": platform}, plat=platform)
        if "http_error" not in r:
            return r
        time.sleep(sleep_s)
    sys.exit("bridge stayed contended — try again later or /session/stop")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", required=True, choices=list(NAV_URLS))
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sidecar", type=int, help="sidecar port with /debug/all-cookies")
    src.add_argument("--cookies", help="Playwright-format cookies JSON file")
    ap.add_argument("--bridge", default=BRIDGE)
    args = ap.parse_args()
    globals()["BRIDGE"] = args.bridge.rstrip("/")

    raw = (
        sidecar_cookies(args.sidecar)
        if args.sidecar
        else json.load(open(args.cookies))
    )
    cookies = normalize(raw, args.platform)
    print(f"[1/4] {len(cookies)} cookies ready for {args.platform}")

    r = wait_window(args.platform)
    print(f"[2/4] session: {r.get('status')} (reused={r.get('reused', False)})")

    r = post(f"{BRIDGE}/session/cookies", {"cookies": cookies}, plat=args.platform)
    if "http_error" in r:
        sys.exit(f"inject failed: {r}")
    print(f"[3/4] injected {r.get('added')} cookies")

    url = NAV_URLS[args.platform]
    r = post(f"{BRIDGE}/session/navigate", {"url": url}, plat=args.platform)
    if "http_error" in r:
        sys.exit(f"navigate failed: {r}")
    print(f"      navigated → {r.get('title')!r}")

    marker = LOGIN_MARKERS.get(args.platform)
    if marker:
        r = post(
            f"{BRIDGE}/session/evaluate",
            {"expression": f"!!({marker})"},
            plat=args.platform,
        )
        if r.get("result") is True:
            sys.exit("FAIL: still on a login page — cookies rejected/expired")

    r = post(f"{BRIDGE}/session/extract", {}, plat=args.platform)
    found = r.get("cookies_found") or list(r.get("cookies", {}).keys())
    print(f"[4/4] persisted {len(found)} cookies: {found}")
    if not found:
        sys.exit("FAIL: extraction found no auth cookies")


if __name__ == "__main__":
    main()
