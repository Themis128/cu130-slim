#!/usr/bin/env python3
"""Cloudflare Access ops for cloudless.gr — list apps, probe paths, add bypasses.

Usage:
    python3 scripts/cf_access.py list
    python3 scripts/cf_access.py probe /api/v1/health /api/v1/auth/oauth/facebook/callback
    python3 scripts/cf_access.py bypass social.cloudless.gr/api/v1/example/webhook --name my-bypass
    python3 scripts/cf_access.py review-mode on|off|status

Reads CLOUDFLARE_ACCESS_TOKEN (Access read/write) and CLOUDFLARE_ACCOUNT_ID
from the repo-root .env. Never prints secrets.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

ACCOUNT = "fb7dc7b69b662480cd5961a4d1913c78"
BASE = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/access/apps"


def load_env() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def cf(method: str, url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['CLOUDFLARE_ACCESS_TOKEN']}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode() if body else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"success": False, "errors": [{"message": e.read().decode()[:300]}]}


def cmd_list() -> None:
    d = cf("GET", f"{BASE}?per_page=100")
    if not d.get("success"):
        sys.exit(f"API error: {d.get('errors')}")
    for a in d.get("result") or []:
        decisions = ",".join(p.get("decision", "?") for p in a.get("policies", []))
        print(f"{a['id']}  {a.get('domain','')!r:60}  {a.get('name')}  [{decisions}]")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_no_redirect = urllib.request.build_opener(_NoRedirect)


def cmd_probe(paths: list[str]) -> None:
    for p in paths:
        url = f"https://social.cloudless.gr{p}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) cf-access-probe"},
        )
        try:
            _no_redirect.open(req, timeout=15)
            code = 200
            loc = ""
        except urllib.error.HTTPError as e:
            code = e.code
            loc = e.headers.get("location", "")
        except Exception as e:
            print(f"ERR   {p}  {e}")
            continue
        if code == 302 and "cloudflareaccess" in loc:
            state = "ACCESS-GATED"
        elif code == 302:
            state = "redirect?"
        else:
            state = "origin"
        print(f"{code}  {state:14}  {p}")


def cmd_bypass(domain: str, name: str) -> None:
    body = {
        "name": name,
        "domain": domain,
        "type": "self_hosted",
        "session_duration": "24h",
        "policies": [
            {"name": "Public bypass", "decision": "bypass", "include": [{"everyone": {}}]}
        ],
    }
    d = cf("POST", BASE, body)
    if not d.get("success"):
        sys.exit(f"API error: {d.get('errors')}")
    r = d["result"]
    print(f"created {r['id']}  {r.get('domain')}  ({r.get('name')})")


# ── App-review mode: temporary hostname-wide bypass ───────────────────────
# Meta/TikTok reviewers must reach social.cloudless.gr directly — an Access
# SSO wall reads as "login page" and gets the app rejected. Instead of a
# second app on the same domain (ambiguous precedence), we prepend a
# bypass-Everyone policy to the EXISTING hostname app: policy order decides,
# and deleting the temp policy restores the admin allow-list untouched.

MAIN_APP_ID = "ed95d3d9-2246-4d44-b15c-5c39170b00dd"  # socialauto-app
REVIEW_POLICY = "app-review-temp-bypass"


def _get_main_app() -> dict:
    d = cf("GET", f"{BASE}/{MAIN_APP_ID}")
    if not d.get("success"):
        sys.exit(f"API error: {d.get('errors')}")
    return d["result"]


def _put_main_app(app: dict) -> None:
    # PUT the whole app back minus read-only keys, so mutable settings
    # (auto_redirect_to_identity, cors_headers, skip_interstitial, ...)
    # keep their current values.
    body = {
        k: v for k, v in app.items()
        if k not in {"id", "uid", "aud", "created_at", "updated_at", "account_id"}
    }
    d = cf("PUT", f"{BASE}/{MAIN_APP_ID}", body)
    if not d.get("success"):
        sys.exit(f"API error: {d.get('errors')}")


def _probe_root() -> str:
    req = urllib.request.Request(
        "https://social.cloudless.gr/",
        headers={"User-Agent": "Mozilla/5.0 cf-access-review-mode"},
    )
    try:
        _no_redirect.open(req, timeout=15)
        return "origin(200)"
    except urllib.error.HTTPError as e:
        loc = e.headers.get("location", "")
        return f"{e.code}→access" if "cloudflareaccess" in loc else f"{e.code}"


def cmd_review_mode(action: str) -> None:
    app = _get_main_app()
    policies = app.get("policies", [])
    has_bypass = any(p.get("name") == REVIEW_POLICY for p in policies)
    if action == "status":
        print(f"review bypass: {'ACTIVE' if has_bypass else 'off'}")
        print(f"root probe: {_probe_root()}")
        return
    if action == "on":
        if has_bypass:
            print("already on")
        else:
            app["policies"] = [
                {"name": REVIEW_POLICY, "decision": "bypass",
                 "include": [{"everyone": {}}]},
                *policies,
            ]
            _put_main_app(app)
            print("review bypass enabled")
    elif action == "off":
        if not has_bypass:
            print("already off")
        else:
            app["policies"] = [p for p in policies if p.get("name") != REVIEW_POLICY]
            _put_main_app(app)
            print("review bypass removed")
    else:
        sys.exit("review-mode takes on|off|status")
    print(f"root probe: {_probe_root()}")


if __name__ == "__main__":
    load_env()
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "list":
        cmd_list()
    elif cmd == "probe":
        cmd_probe(args)
    elif cmd == "bypass":
        name = "bypass"
        if "--name" in args:
            i = args.index("--name")
            name = args[i + 1]
            args = args[:i] + args[i + 2:]
        if not args:
            sys.exit("bypass requires a domain/path, e.g. host/api/v1/x")
        cmd_bypass(args[0], name)
    elif cmd == "review-mode":
        cmd_review_mode(args[0] if args else "status")
    else:
        sys.exit(__doc__)
