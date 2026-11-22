#!/usr/bin/env python3
"""meta_cookie_sync.py — persist Meta (Instagram/Facebook/Threads) browser
sessions into the canonical credential store and re-inject them into the
debug Chrome, mirroring the X_WEB_COOKIES_JSON pattern.

Why: Meta sessions die silently (password rotation, checkpoint purges,
"user changed their password" token revocations). Re-login is the failure
mode — a captured session cookie set restores the lane without touching
2FA. Same reasoning as session-persistence-ops Pattern 2.

Surfaces:
  --export   dump instagram.com + facebook.com cookies from debug Chrome
             (:9229) -> writes C:\\Users\\tbaltzakis\\meta_cookies.json
             (0600) and, with --store, IG_COOKIES_JSON / FB_COOKIES_JSON
             into the secret store via cred_set.py (stdin, never argv).
  --inject   push the stored cookie sets back into debug Chrome.
  --check    report session-cookie presence per domain (no secrets).

Session validity markers:
  instagram.com : sessionid + ds_user_id
  facebook.com  : c_user + xs
  threads.net   : session cookies ride the IG session; exported for
                  completeness but IG is the auth root.

Never prints cookie values — only counts and sha256 prefixes.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.request

REPO = os.path.expanduser("~/cu130-slim")
ENV_FILE = os.path.join(REPO, ".env")
CDP_URL = os.environ.get("BROWSER_CDP_URL", "http://192.168.96.1:9229")
META_FILE = "/mnt/c/Users/tbaltzakis/meta_cookies.json"
CRED_SET = os.path.join(
    REPO, ".devin/skills/platform-cred-store-ops/scripts/cred_set.py")

# domain suffix -> env key holding a flat {name: value} cookie dict
DOMAINS = {
    "instagram.com": "IG_COOKIES_JSON",
    "facebook.com": "FB_COOKIES_JSON",
    "threads.net": "THREADS_COOKIES_JSON",
}
REQUIRED = {
    "instagram.com": ("sessionid", "ds_user_id"),
    "facebook.com": ("c_user", "xs"),
    "threads.net": ("sessionid",),
}


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


def _tabs() -> list[dict]:
    return [t for t in json.loads(
        urllib.request.urlopen(CDP_URL + "/json/list", timeout=10).read())
        if t.get("type") == "page"]


def _get_all_cookies() -> dict[str, dict[str, str]]:
    """Return {domain_suffix: {cookie_name: value}} for Meta domains."""
    import websocket  # websocket-client
    tabs = _tabs()
    if not tabs:
        print("no CDP tabs — is debug Chrome up on :9229?", file=sys.stderr)
        sys.exit(2)
    ws = websocket.create_connection(
        tabs[0]["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
    mid = [0]

    def call(method, **params):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == mid[0]:
                return m.get("result", {})

    out: dict[str, dict[str, str]] = {}
    cookies = call("Storage.getCookies").get("cookies", [])
    ws.close()
    for c in cookies:
        dom = c.get("domain", "").lstrip(".")
        for suffix in DOMAINS:
            if dom == suffix or dom.endswith("." + suffix):
                out.setdefault(suffix, {})[c["name"]] = c.get("value", "")
    return out


def _fp(obj: dict) -> str:
    blob = json.dumps(obj, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def cmd_export(env: dict, store: bool) -> int:
    jars = _get_all_cookies()
    payload = {}
    for suffix, key in DOMAINS.items():
        jar = jars.get(suffix, {})
        have = [n for n in REQUIRED[suffix] if jar.get(n)]
        missing = [n for n in REQUIRED[suffix] if not jar.get(n)]
        status = "LIVE" if not missing else ("partial" if have else "none")
        print(f"{suffix}: {status} ({len(jar)} cookies, "
              f"have {len(have)}/{len(REQUIRED[suffix])} markers)")
        if jar:
            payload[suffix] = jar
    if not payload:
        print("no Meta cookies found — nothing to export")
        return 1
    tmp = META_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, META_FILE)
    print(f"wrote {META_FILE} fp={_fp(payload)}")
    if store:
        for suffix, jar in payload.items():
            key = DOMAINS[suffix]
            flat = {k: v for k, v in jar.items() if v}
            p = subprocess.run(
                [sys.executable, CRED_SET, key, "--value-stdin"],
                input=json.dumps(flat), text=True, capture_output=True)
            print(f"store {key}: {'ok' if p.returncode == 0 else 'FAIL'}")
            if p.returncode != 0:
                print(p.stderr[-300:], file=sys.stderr)
                return 1
    return 0


def _cookies_for_inject(env: dict, suffix: str) -> dict:
    """Resolve a cookie dict for one domain: env key wins, else meta file."""
    raw = env.get(DOMAINS[suffix], "")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    if os.path.exists(META_FILE):
        try:
            return json.load(open(META_FILE)).get(suffix, {})
        except Exception:
            pass
    return {}


def cmd_inject(env: dict) -> int:
    import websocket
    ws_url = None
    for t in _tabs():
        ws_url = t["webSocketDebuggerUrl"]
        break
    if not ws_url:
        print("no CDP tabs", file=sys.stderr)
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

    total = 0
    for suffix in DOMAINS:
        cookies = _cookies_for_inject(env, suffix)
        if not cookies:
            print(f"{suffix}: no stored cookies, skip")
            continue
        ok = 0
        for name, value in cookies.items():
            if not value:
                continue
            r = call("Network.setCookie", name=name, value=value,
                     domain="." + suffix, path="/", secure=True)
            if isinstance(r, dict) and r.get("success"):
                ok += 1
        print(f"{suffix}: {ok}/{len(cookies)} cookies injected")
        total += ok
    print(f"total injected: {total}")
    ws.close()
    return 0 if total else 1


def cmd_check(env: dict) -> int:
    jars = _get_all_cookies()
    bad = 0
    for suffix, key in DOMAINS.items():
        jar = jars.get(suffix, {})
        live = all(jar.get(n) for n in REQUIRED[suffix])
        stored = bool(env.get(key)) or (
            os.path.exists(META_FILE)
            and suffix in json.load(open(META_FILE)))
        mark = "OK" if live else ("stored-only" if stored else "MISSING")
        if not live:
            bad += 1
        print(f"{suffix}: {mark}")
    return 1 if bad else 0


def main() -> int:
    env = load_env()
    args = set(sys.argv[1:])
    if "--export" in args:
        return cmd_export(env, "--store" in args)
    if "--inject" in args:
        return cmd_inject(env)
    return cmd_check(env)


if __name__ == "__main__":
    sys.exit(main())
