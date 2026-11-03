#!/usr/bin/env python3
"""cf_email_destination.py — verify an Email Routing destination for send_email.

Outbound send_email refuses recipients that aren't verified routing
destinations. This automates the whole chain:

  1. list existing destinations for the zone (CF API)
  2. create one if missing (CF emails a confirmation link)
  3. poll the omv dovecot mailbox for the confirmation mail
  4. follow the verify link
  5. re-check destination status

Env (read from .env — never printed):
  CLOUDFLARE_API_TOKEN (or CF_API_TOKEN) — Email Routing edit on the zone
  CLOUDFLARE_ZONE_ID or resolved via GET /zones?name=cloudless.gr
  OMV_IMAP_HOST / OMV_IMAP_USER / OMV_IMAP_PASS — dovecot mailbox that
    receives *@cloudless.gr (mail-ingest forwards there)

Usage: python backend/scripts/cf_email_destination.py [--email tbaltzakis@cloudless.gr] [--json]
"""

from __future__ import annotations

import argparse
import imaplib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

CF = "https://api.cloudflare.com/client/v4"
env_file = Path(__file__).resolve().parents[3] / ".env"


def _env(key: str, default: str = "") -> str:
    if val := os.environ.get(key):
        return val.strip()
    try:
        for line in env_file.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def _api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        CF + path,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(payload).encode() if payload else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"success": False, "errors": [{"message": e.read().decode()[:300]}]}


def _zone_id(token: str) -> str | None:
    zid = _env("CLOUDFLARE_ZONE_ID") or _env("CF_ZONE_ID")
    if zid:
        return zid
    res = _api("GET", "/zones?name=cloudless.gr", token)
    res = res if isinstance(res, dict) else {}
    items = (res.get("result") or [])
    return items[0]["id"] if items else None


def _destinations(token: str, zone: str) -> list[dict]:
    res = _api("GET", f"/zones/{zone}/email/routing/addresses", token)
    return res.get("result") or []


def _poll_mailbox(host: str, user: str, pw: str, email: str, tries: int = 20, wait: int = 15) -> str | None:
    """Poll the dovecot INBOX for the CF verification mail; return the verify URL."""
    for _ in range(tries):
        try:
            box = imaplib.IMAP4_SSL(host, 993)
            box.login(user, pw)
            box.select("INBOX")
            typ, ids = box.search(None, '(FROM "cloudflare" UNSEEN)')
            for mid in (ids[0].split() if typ == "OK" else []):
                typ, data = box.fetch(mid, "(RFC822)")
                body = data[0][1].decode("utf-8", "replace") if typ == "OK" else ""
                m = re.search(r"https://[^\"'\s>]+verify[^\"'\s<]+", body)
                if m and email.split("@")[0] in body:
                    box.logout()
                    return m.group(0)
            box.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
        time.sleep(wait)
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--email", default="tbaltzakis@cloudless.gr")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    token = _env("CLOUDFLARE_API_TOKEN") or _env("CF_API_TOKEN")
    rows: list[dict] = []

    def say(name, ok, detail, fix=""):
        rows.append({"check": name, "status": "ok" if ok else "action", "detail": detail, "fix": fix})

    if not token:
        say("cf-token", False, "no CLOUDFLARE_API_TOKEN/CF_API_TOKEN in env",
            "add an API token with Email Routing edit to .env, re-run")
        return _report(rows, args.json)
    zone = _zone_id(token)
    if not zone:
        say("zone", False, "cloudless.gr zone id not resolvable", "check token zone read scope")
        return _report(rows, args.json)
    say("zone", True, zone)

    dests = _destinations(token, zone)
    mine = [d for d in dests if d.get("email", "").lower() == args.email.lower()]
    if mine:
        d = mine[0]
        if d.get("verified"):
            say("destination", True, f"{args.email} already verified")
            return _report(rows, args.json)
        say("destination", False, f"{args.email} exists, unverified — resending not needed, polling mailbox")
    else:
        res = _api("POST", f"/zones/{zone}/email/routing/addresses", token, {"email": args.email})
        if not res.get("success"):
            say("create-destination", False, str(res.get("errors"))[:200],
                "check token Email Routing write scope")
            return _report(rows, args.json)
        say("create-destination", True, f"created {args.email} — confirmation mail sent")

    host, user, pw = _env("OMV_IMAP_HOST"), _env("OMV_IMAP_USER"), _env("OMV_IMAP_PASS")
    if not (host and user and pw):
        say("mailbox", False, "OMV_IMAP_* env missing — can't auto-read confirmation",
            "add OMV_IMAP_HOST/USER/PASS to .env (dovecot on omv-ha) or click the link in the mail manually")
        return _report(rows, args.json)
    link = _poll_mailbox(host, user, pw, args.email)
    if not link:
        say("mailbox", False, "no confirmation mail found in 5min",
            f"check the {args.email} mailbox manually for the CF verify link")
        return _report(rows, args.json)
    try:
        urllib.request.urlopen(urllib.request.Request(link), timeout=20)
        say("confirm-link", True, "followed")
    except Exception as exc:
        say("confirm-link", False, str(exc)[:150], "open the link manually")
        return _report(rows, args.json)

    dests = _destinations(token, zone)
    ok = any(d.get("email", "").lower() == args.email.lower() and d.get("verified") for d in dests)
    say("verified", ok, "verified" if ok else "still unverified",
        "" if ok else "destination may take a minute — re-run")
    return _report(rows, args.json)


def _report(rows: list[dict], as_json: bool) -> int:
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            mark = "ok " if r["status"] == "ok" else "FIX"
            print(f"[{mark}] {r['check']:<20} {r['detail']}")
            if r["fix"]:
                print(f"      -> {r['fix']}")
    return 0 if all(r["status"] == "ok" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
