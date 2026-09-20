#!/usr/bin/env python3
"""Cloudflare Access ops for cloudless.gr — list apps, probe paths, add bypasses.

Usage:
    python3 scripts/cf_access.py list
    python3 scripts/cf_access.py probe /api/v1/health /api/v1/auth/oauth/facebook/callback
    python3 scripts/cf_access.py bypass social.cloudless.gr/api/v1/example/webhook --name my-bypass

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


def cmd_probe(paths: list[str]) -> None:
    for p in paths:
        url = f"https://social.cloudless.gr{p}"
        req = urllib.request.Request(url)
        try:
            urllib.request.urlopen(req, timeout=15)
            code = 200
            loc = ""
        except urllib.error.HTTPError as e:
            code = e.code
            loc = e.headers.get("location", "")
        except Exception as e:
            print(f"ERR   {p}  {e}")
            continue
        state = "ACCESS-GATED" if code == 302 and "cloudflareaccess" in loc else "origin" if code != 302 else "redirect?"
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
    else:
        sys.exit(__doc__)
