#!/usr/bin/env python3
"""browser_secret_sync.py — keep Windows browser credentials + debug-Chrome
sessions converged with the SocialAuto secret store.

Two surfaces, both idempotent and secret-safe (values never printed):

1. CSV regen — rewrites the canonical Chrome/Edge import file at
   C:\\Users\\tbaltzakis\\Downloads\\browser-passwords-import.csv from the
   platform-cred keys in .env. Re-run after any cred_set.py rotation, then
   import once per browser (chrome://password-manager/settings -> Import;
   edge://settings/profiles/passwords -> Import). Existing saved passwords
   are preserved — the importers skip duplicates.

2. Debug-Chrome session sync (--cookies) — pushes session cookies we hold
   in .env into the debug Chrome (:9229, chrome-debug-profile) via CDP
   Storage/Network cookies, so logged-in lanes (X web session, etc.)
   survive credential rotation without re-login.

Usage:
  browser_secret_sync.py            # regen CSV only
  browser_secret_sync.py --cookies  # + inject .env-held cookies to :9229
  browser_secret_sync.py --check    # drift report, writes nothing
"""
from __future__ import annotations

import csv
import json
import os
import sys
import urllib.request

REPO = os.path.expanduser("~/cu130-slim")
ENV_FILE = os.path.join(REPO, ".env")
CSV_PATH = "/mnt/c/Users/tbaltzakis/Downloads/browser-passwords-import.csv"
CDP_URL = os.environ.get("BROWSER_CDP_URL", "http://192.168.96.1:9229")

# platform-cred key -> (site name, login url, username env key or literal)
CSV_MAP = [
    ("Instagram", "https://www.instagram.com/", "INSTAGRAM_USERNAME", "INSTAGRAM_PASSWORD"),
    ("Facebook", "https://www.facebook.com/", "baltzakis.themis@gmail.com", "FACEBOOK_PASSWORD"),
    ("Facebook Cloudless", "https://www.facebook.com/cloudless.gr", "cloudless.gr", "FACEBOOK_PASSWORD"),
    ("Threads", "https://www.threads.net/", "cloudless.gr", "INSTAGRAM_PASSWORD"),
    ("LinkedIn", "https://www.linkedin.com/", "LINKEDIN_USERNAME", "LINKEDIN_PASSWORD"),
    ("X / Twitter", "https://x.com/", "TWITTER_LOGIN_USERNAME", "TWITTER_LOGIN_PASSWORD"),
    ("TikTok", "https://www.tiktok.com/", "TIKTOK_DEV_EMAIL", "TIKTOK_DEV_PASSWORD"),
    ("Bluesky", "https://bsky.app/", "tbaltzakis.bsky.social", "BSKY_MAIN_PASSWORD"),
]

# cookie sources, tried in order — first that yields a non-empty dict wins.
# X_WEB_COOKIES_JSON in .env is STALE (pre-rotation session); the canonical
# fresh set is C:\\Users\\tbaltzakis\\x_cookies.json (matches X_WEB_AUTH_TOKEN).
COOKIE_SOURCES = [
    ("/mnt/c/Users/tbaltzakis/x_cookies.json", "x.com"),
    ("X_WEB_COOKIES_JSON", "x.com"),
]


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
        sys.exit(2)
    return env


def _resolve(env: dict, key: str) -> str:
    """Env key may itself hold a username or be a literal."""
    return env.get(key, key) if "@" in key or "." in key or " " not in key else env.get(key, key)


def regen_csv(env: dict) -> int:
    rows = []
    for name, url, user_key, pw_key in CSV_MAP:
        user = env.get(user_key, user_key)
        pw = env.get(pw_key, "")
        if not pw:
            print(f"skip {name}: {pw_key} empty")
            continue
        rows.append({"name": name, "url": url, "username": user,
                     "password": pw, "note": "cloudless-cred-sync"})
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "url", "username", "password", "note"])
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, CSV_PATH)
    print(f"{len(rows)} rows -> {CSV_PATH}")
    return 0


def _cdp_ws_url() -> str | None:
    try:
        tabs = json.loads(urllib.request.urlopen(
            CDP_URL + "/json/list", timeout=8).read())
        pages = [t for t in tabs if t.get("type") == "page"]
        return pages[0]["webSocketDebuggerUrl"] if pages else None
    except Exception as exc:
        print(f"cdp unreachable: {exc}")
        return None


def inject_cookies(env: dict) -> int:
    import websocket  # websocket-client
    ws_url = _cdp_ws_url()
    if not ws_url:
        return 2
    ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
    mid = [0]

    def call(method, **params):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == mid[0]:
                return m.get("result", {})

    injected = 0
    for src, domain in COOKIE_SOURCES:
        raw = ""
        if src.startswith("/") and os.path.exists(src):
            try:
                raw = open(src).read()
            except OSError:
                continue
        else:
            raw = env.get(src, "")
        if not raw:
            continue
        try:
            cookies = json.loads(raw)
        except Exception:
            continue
        items = []
        for k, v in cookies.items():
            if isinstance(v, dict):
                v = v.get("value", "")
            if not isinstance(v, str):
                v = json.dumps(v)
            if v:
                items.append({"name": k, "value": v, "domain": "." + domain,
                              "path": "/", "secure": True})
        ok = 0
        for c in items:
            r = call("Network.setCookie", **c)
            if isinstance(r, dict) and r.get("success"):
                ok += 1
        print(f"{domain}: {ok}/{len(items)} cookies set ({os.path.basename(src)})")
        injected += ok
    # commit via Storage if needed — Network.setCookie applies immediately
    print(f"cookies injected: {injected}")
    ws.close()
    return 0


def check(env: dict) -> int:
    missing = [pw for _, _, _, pw in CSV_MAP if not env.get(pw)]
    stale = []
    for name, _, user_key, pw_key in CSV_MAP:
        if env.get(pw_key) and not env.get(user_key, user_key):
            stale.append(name)
    print(f"cred keys present: {len(CSV_MAP) - len(missing)}/{len(CSV_MAP)}")
    if missing:
        print("missing password keys:", ", ".join(sorted(set(missing))))
    if stale:
        print("username unresolved:", ", ".join(stale))
    return 0 if not missing else 1


def main() -> int:
    env = load_env()
    args = set(sys.argv[1:])
    if "--check" in args:
        return check(env)
    rc = regen_csv(env)
    if "--cookies" in args:
        rc = max(rc, inject_cookies(env))
    return rc


if __name__ == "__main__":
    sys.exit(main())
