#!/usr/bin/env python3
"""fb_token_watch.py — fire the FB OAuth gate the moment Chrome :9222 is live.

The expired facebook-personal token needs ONE OAuth authorize click in a
browser where facebook.com is already logged in. This watcher polls the
Windows CDP endpoint; when the debug/real Chrome appears it mints the
authorize URL from the live env and runs `gate_runner.py fb-token <url>`
which does approve → capture → omv secrets + secret_store, then exits 0.

Run detached (Windows schtask or `nohup ... &`):
    python fb_token_watch.py [--real] [--timeout 21600] [--interval 60]

Flags mirror gate_runner: --real drives the real profile (default is the
persistent debug profile). Exits 2 on timeout so a supervisor can alert.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CDP = os.environ.get("FB_TOKEN_CDP", "http://localhost:9222")
SOCIAL_API = os.environ.get("SOCIAL_API_URL", "http://localhost:8083")


def cdp_live() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP}/json/version", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def fb_expired() -> bool:
    """Probe Postgres directly — the accounts API needs auth the watcher
    doesn't have. docker exec keeps it credential-free."""
    import subprocess

    try:
        out = subprocess.run(
            [
                "docker", "exec", "social-postgres", "psql",
                "-U", "social_user", "-d", "social_automation", "-tAc",
                "SELECT count(*) FROM social_accounts "
                "WHERE platform='facebook' AND status='expired'",
            ],
            capture_output=True, text=True, timeout=15,
        )
        return out.stdout.strip() not in ("", "0")
    except Exception:
        return False  # can't prove expired → don't fire a consent gate blindly


def oauth_url() -> str:
    cid = os.environ["FACEBOOK_CLIENT_ID"]
    redirect = os.environ.get(
        "FACEBOOK_REDIRECT_URI",
        "https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback",
    )
    # business_management included so the healed token can also mint the
    # never-expiring system-user token for WhatsApp/Meta (meta_system_user.py).
    scopes = "pages_manage_posts,pages_read_engagement,pages_show_list,public_profile,business_management"
    return (
        f"https://www.facebook.com/dialog/oauth?client_id={cid}"
        f"&redirect_uri={redirect}&scope={scopes}&response_type=code"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--timeout", type=int, default=21600)  # 6h
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()

    deadline = time.monotonic() + args.timeout
    print(f"[fb-token-watch] polling {CDP} every {args.interval}s (timeout {args.timeout}s)")
    while time.monotonic() < deadline:
        if cdp_live():
            print("[fb-token-watch] chrome live — checking FB account state")
            if not fb_expired():
                print("[fb-token-watch] no expired facebook account — nothing to do")
                return 0
            cmd = [sys.executable, str(HERE / "gate_runner.py"), "fb-token", oauth_url()]
            if args.real:
                cmd.append("--real")
            print(f"[fb-token-watch] firing {' '.join(cmd[:5])} <oauth_url> …")
            r = subprocess.run(cmd, timeout=900)
            return r.returncode
        time.sleep(args.interval)
    print("[fb-token-watch] TIMEOUT — chrome never came up", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
