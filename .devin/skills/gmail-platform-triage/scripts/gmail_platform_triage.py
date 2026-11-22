#!/usr/bin/env python3
"""gmail_platform_triage — scan/trash platform-related mail in a Gmail mailbox.

Auth: reads .env for GMAIL_OAUTH_CLIENT_ID/SECRET plus a token JSON.
  - GMAIL_SEND_TOKEN_JSON  (scope gmail.modify) → scan + trash
  - GMAIL_TOKEN_JSON       (scope gmail.readonly) → scan only
  - GMAIL_REFRESH_TOKEN[_THEMIS_BALTZAKIS] → raw refresh token (readonly lane)

Usage:
    gmail_platform_triage.py scan [--days 60] [--mailbox me]
    gmail_platform_triage.py trash --categories meta,cloudflare,github [--days 60] [--yes]
    gmail_platform_triage.py trash --all-addressed --days 60 --yes

Categories are query groups over platform senders. `trash` moves matching
messages to Gmail Trash (recoverable ~30d) via batchModify — it never
deletes permanently. Security-sensitive categories (account security alerts,
device verification, password resets) are excluded from --all-addressed and
must be named explicitly.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://gmail.googleapis.com/gmail/v1/users"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ENV_PATHS = [
    Path(__file__).resolve().parents[5] / ".env",          # repo root .env
    Path.home() / "cu130-slim" / ".env",
]

# Platform sender groups. `security=True` categories are excluded from
# --all-addressed (device verifies, password resets, unusual-login alerts).
CATEGORIES: dict[str, dict] = {
    "meta":        {"q": "from:(meta.com OR facebookmail.com OR facebook.com OR instagram.com OR threads.net OR fb.com)", "security": False},
    "linkedin":    {"q": "from:linkedin.com", "security": False},
    "linkedin-sec": {"q": "from:linkedin.com (subject:(verify OR security OR password OR sign in) OR from:security-noreply@linkedin.com)", "security": True},
    "tiktok-dev":  {"q": "from:dev.tiktok.com", "security": False},
    "tiktok-app":  {"q": "from:(service.tiktok.com OR mail.tiktok.com OR tiktok.com) -from:dev.tiktok.com", "security": False},
    "x":           {"q": "from:(x.com OR twitter.com)", "security": False},
    "cloudflare":  {"q": "from:cloudflare.com", "security": False},
    "vercel":      {"q": "from:vercel.com", "security": False},
    "github":      {"q": "from:(github.com OR githubsupport.com)", "security": False},
    "google-dev":  {"q": "from:(google.com OR googleapis.com) subject:(developer OR cloud OR api OR workspace OR alert OR payments OR newsletter)", "security": False},
    "google-sec":  {"q": "from:no-reply@accounts.google.com", "security": True},
    "slack":       {"q": "from:slack.com", "security": False},
    "polar":       {"q": "from:(polar.sh OR polar.com)", "security": False},
    "resend":      {"q": "from:(resend.com OR notifications.resend.com OR updates.resend.com)", "security": False},
    "calendly":    {"q": "from:(calendly.com OR cal.com)", "security": False},
    "whatsapp":    {"q": "from:(whatsapp.com OR whatsapp.net)", "security": False},
    "viber":       {"q": "from:viber.com", "security": False},
    "bluesky":     {"q": "from:(bsky.social OR blueskyweb.xyz)", "security": False},
    "telegram":    {"q": "from:telegram.org", "security": False},
}


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for p in ENV_PATHS:
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
        if env.get("GMAIL_OAUTH_CLIENT_ID"):
            break
    return env


def _access_token(env: dict[str, str]) -> tuple[str, bool]:
    """Return (access_token, can_modify)."""
    cid, csec = env.get("GMAIL_OAUTH_CLIENT_ID"), env.get("GMAIL_OAUTH_CLIENT_SECRET")

    def _refresh(rt: str) -> str:
        data = urllib.parse.urlencode({
            "client_id": cid, "client_secret": csec,
            "refresh_token": rt, "grant_type": "refresh_token",
        }).encode()
        r = urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data), timeout=20)
        return json.loads(r.read())["access_token"]

    # Prefer the modify-scoped token so trash works.
    for key, can_mod in (("GMAIL_SEND_TOKEN_JSON", True), ("GMAIL_TOKEN_JSON", False)):
        raw = env.get(key)
        if not raw:
            continue
        try:
            rt = json.loads(raw)["refresh_token"]
        except Exception:
            rt = raw
        return _refresh(rt), can_mod
    for key in ("GMAIL_REFRESH_TOKEN", "GMAIL_REFRESH_TOKEN_THEMIS_BALTZAKIS"):
        if env.get(key):
            return _refresh(env[key]), False
    raise SystemExit("No Gmail token in .env (GMAIL_SEND_TOKEN_JSON / GMAIL_TOKEN_JSON / GMAIL_REFRESH_TOKEN)")


def _api(at: str, path: str, method: str = "GET", body: dict | None = None):
    req = urllib.request.Request(
        f"{API}/me/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {at}", "Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _hdr(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _search(at: str, query: str, max_results: int = 500) -> list[dict]:
    out, page = [], None
    while len(out) < max_results:
        params = {"q": query, "maxResults": min(100, max_results - len(out))}
        if page:
            params["pageToken"] = page
        res = _api(at, "messages?" + urllib.parse.urlencode(params))
        out.extend(res.get("messages", []))
        page = res.get("nextPageToken")
        if not page:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["scan", "trash"])
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--categories", default="", help="comma list; default for trash = all non-security")
    ap.add_argument("--all-addressed", action="store_true", help="trash every non-security category")
    ap.add_argument("--include-security", action="store_true", help="also trash security categories when named")
    ap.add_argument("--limit", type=int, default=500, help="max messages per category")
    ap.add_argument("--yes", action="store_true", help="required for trash")
    args = ap.parse_args()

    env = _load_env()
    at, can_mod = _access_token(env)
    me = _api(at, "profile").get("emailAddress", "?")
    print(f"mailbox: {me}  scope: {'gmail.modify' if can_mod else 'READ-ONLY (trash unavailable)'}")

    if args.cmd == "scan":
        for name, cat in CATEGORIES.items():
            q = f"{cat['q']} newer_than:{args.days}d"
            try:
                msgs = _search(at, q, args.limit)
            except Exception as e:
                print(f"{name:15} ERR {str(e)[:60]}")
                continue
            sec = " [security]" if cat["security"] else ""
            print(f"{name:15} {len(msgs):4} msgs{sec}")
            for m in msgs[:5]:
                f = _api(at, f"messages/{m['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date")
                print(f"    { _hdr(f,'Date')[:16]} | {_hdr(f,'From')[:40]} | {_hdr(f,'Subject')[:65]}")
        return 0

    # trash
    if not can_mod:
        raise SystemExit("Token is gmail.readonly — trash needs GMAIL_SEND_TOKEN_JSON (gmail.modify).")
    if args.all_addressed:
        cats = [n for n, c in CATEGORIES.items() if not c["security"]]
    elif args.categories:
        cats = [c.strip() for c in args.categories.split(",") if c.strip()]
        for c in cats:
            if CATEGORIES.get(c, {}).get("security") and not args.include_security:
                raise SystemExit(f"category '{c}' is security-sensitive — pass --include-security to trash it")
    else:
        raise SystemExit("trash needs --categories=a,b or --all-addressed")

    total = 0
    for name in cats:
        cat = CATEGORIES.get(name)
        if not cat:
            print(f"unknown category: {name}")
            continue
        msgs = _search(at, f"{cat['q']} newer_than:{args.days}d", args.limit)
        ids = [m["id"] for m in msgs]
        if not ids:
            print(f"{name:15} 0 msgs")
            continue
        if not args.yes:
            print(f"{name:15} would trash {len(ids)} msgs (dry-run; pass --yes)")
            total += len(ids)
            continue
        for i in range(0, len(ids), 1000):
            _api(at, "messages/batchModify", method="POST",
                 body={"ids": ids[i:i + 1000], "addLabelIds": ["TRASH"]})
        print(f"{name:15} trashed {len(ids)} msgs")
        total += len(ids)
    print(f"total: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
