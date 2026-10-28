#!/usr/bin/env python3
"""One-shot health check of ALL SocialAuto social sessions.

Covers three layers:
  a) OAuth accounts in social-postgres (social_accounts) — token status,
     expiry, and whether a refresh token is stored. Expired accounts with a
     refresh token are auto-healed by the hourly `refresh_expiring_tokens`
     task; expired accounts WITHOUT one need manual reconnection.
  b) The browser-novnc bridge (http://localhost:9223) — /health and the
     current /session/status.
  c) Per-platform browser sidecars — GET /health each.

Usage:
    python3 scripts/session_health.py            # readable table
    python3 scripts/session_health.py check      # same thing
    python3 scripts/session_health.py --json     # machine-readable output

Exit codes: 0 = all ok, 1 = at least one hard failure
(sidecar down, bridge down, expired account without refresh token).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PSQL = [
    "docker", "compose", "exec", "-T", "social-postgres",
    "psql", "-U", "social_user", "-d", "social_automation", "-t", "-A", "-F", "\t",
]

BRIDGE = "http://localhost:9223"
SIDECARS = [
    ("tiktok", "http://localhost:9224"),
    ("linkedin", "http://localhost:9225"),
    ("facebook", "http://localhost:9226"),
    ("messenger", "http://localhost:9230"),
]


def sql(query: str) -> list[list[str]]:
    res = subprocess.run(
        [*PSQL, "-c", query], cwd=ROOT, capture_output=True, text=True
    )
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
        sys.exit(1)
    return [r.split("\t") for r in res.stdout.strip().splitlines() if r.strip()]


def http_json(url: str, timeout: float = 5.0) -> tuple[int | None, dict]:
    """GET url -> (status, json). status None on connection failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"error": str(e)}


def check_accounts() -> tuple[list[dict], list[str], list[str]]:
    """Return (accounts, hard_failures, notes)."""
    rows = sql(
        """SELECT platform, account_id, coalesce(username,''),
                  coalesce(display_name,''), status,
                  coalesce(token_expires_at::text,''),
                  (refresh_token_enc IS NOT NULL)
           FROM social_accounts ORDER BY platform, username"""
    )
    accounts, failures, notes = [], [], []
    for platform, account_id, username, display, status, expires, has_refresh in rows:
        has_refresh = has_refresh == "t"
        name = display or username or account_id
        note = ""
        if status == "expired":
            if has_refresh:
                note = "expired — has refresh token, will auto-heal (hourly refresh_expiring_tokens)"
                notes.append(f"{platform}/{name}: {note}")
            else:
                note = "ACTION REQUIRED — expired with NO refresh token, reconnect manually"
                failures.append(f"{platform}/{name}: {note}")
        accounts.append(
            {
                "platform": platform,
                "account_id": account_id,
                "name": name,
                "status": status,
                "token_expires_at": expires,
                "has_refresh": has_refresh,
                "note": note,
            }
        )
    return accounts, failures, notes


def check_bridge() -> tuple[dict, list[str]]:
    """Return (bridge_info, failures)."""
    failures = []
    code, health = http_json(f"{BRIDGE}/health")
    if code != 200:
        return {"reachable": False}, [f"browser bridge {BRIDGE} down: {health.get('error') or code}"]

    _, status = http_json(f"{BRIDGE}/session/status")
    # page URL is best-effort: /session/page-info needs a live page and is
    # subject to the busy-hold, so tolerate 400/409 silently.
    page_url = ""
    c, info = http_json(f"{BRIDGE}/session/page-info")
    if c == 200 and isinstance(info, dict):
        page_url = info.get("url", "")
    return {
        "reachable": True,
        "health": health,
        "platform": status.get("platform"),
        "session_status": status.get("status"),
        "message": status.get("message", ""),
        "page_url": page_url,
    }, failures


def check_sidecars() -> tuple[list[dict], list[str]]:
    results, failures = [], []
    for name, base in SIDECARS:
        code, body = http_json(f"{base}/health")
        ok = code == 200 and body.get("status") == "ok"
        if not ok:
            failures.append(f"{name} sidecar {base} down: {body.get('error') or code}")
        results.append({"name": name, "url": base, "ok": ok, "detail": body})
    return results, failures


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cmd", nargs="?", default="check", choices=["check"], help="default: check")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    accounts, acct_failures, acct_notes = check_accounts()
    bridge, bridge_failures = check_bridge()
    sidecars, side_failures = check_sidecars()

    hard = acct_failures + bridge_failures + side_failures
    attention = hard + acct_notes

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not hard,
                    "accounts": accounts,
                    "bridge": bridge,
                    "sidecars": sidecars,
                    "needs_attention": attention,
                },
                indent=2,
            )
        )
        return 1 if hard else 0

    print("=== OAuth accounts (social_accounts) ===")
    print(f"{'platform':<12}{'name':<28}{'status':<10}{'token_expires_at':<34}{'refresh':<8}note")
    for a in accounts:
        print(
            f"{a['platform']:<12}{a['name'][:27]:<28}{a['status']:<10}"
            f"{a['token_expires_at'][:33]:<34}{str(a['has_refresh']).lower():<8}{a['note']}"
        )

    print("\n=== browser-novnc bridge (:9223) ===")
    if bridge.get("reachable"):
        print(
            f"health=ok  session platform={bridge.get('platform')} "
            f"status={bridge.get('session_status')}  msg={bridge.get('message')}"
        )
        if bridge.get("page_url"):
            print(f"page_url={bridge['page_url']}")
    else:
        print("bridge DOWN")

    print("\n=== sidecars ===")
    for s in sidecars:
        state = "ok" if s["ok"] else "DOWN"
        extra = ""
        d = s["detail"]
        if "has_session" in d:
            extra += f" has_session={d['has_session']}"
        if d.get("rate_limited"):
            extra += " rate_limited=true"
        print(f"{s['name']:<10}{s['url']:<28}{state}{extra}")

    print("\n=== Needs attention ===")
    if attention:
        for line in attention:
            print(f"  - {line}")
    else:
        print("  (none)")

    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
