#!/usr/bin/env python3
"""TikTok browser sidecar session helpers.
Usage: sidecar-session.py status|ensure"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (NODE_WORK, OUT_DIR, SIDECAR, ensure_playwright,  # noqa: E402
                     env_key, run_mjs)
from skill_http import usage  # noqa: E402


def status() -> None:
    for path in ("/health", "/session"):
        try:
            with urllib.request.urlopen(f"{SIDECAR}{path}", timeout=10) as r:
                print(json.dumps(json.loads(r.read().decode()), indent=2))
        except Exception:
            pass


action = sys.argv[1] if len(sys.argv) > 1 else "status"
if action == "status":
    status()
elif action == "ensure":
    NODE_WORK.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_playwright()
    if not env_key("TIKTOK_DEV_EMAIL") or not env_key("TIKTOK_DEV_PASSWORD"):
        print("Need TIKTOK_DEV_EMAIL and TIKTOK_DEV_PASSWORD in .env",
              file=sys.stderr)
        sys.exit(1)
    rc = run_mjs("ensure-session.mjs", extra_env={"TIKTOK_SIDECAR_URL": SIDECAR})
    status()
    sys.exit(rc)
else:
    usage("sidecar-session.py status|ensure")
