#!/usr/bin/env python3
"""app_audit.py — consolidated developer-app audit across all platforms.

Runs each *_tool.py inside social-api, prints a normalized status table,
exits 1 on any expired/revoked/unverified signal.

    app_audit.py            # table
    app_audit.py --json     # machine-readable
"""
from __future__ import annotations

import json
import subprocess
import sys

TOOLS = {
    "meta":      ["python", "scripts/meta_tool.py", "accounts"],
    "linkedin":  ["python", "scripts/linkedin_tool.py", "accounts"],
    "x":         ["python", "scripts/x_tool.py", "validate"],
    "tiktok":    ["python", "scripts/tiktok_tool.py", "validate"],
    "whatsapp":  ["python", "scripts/whatsapp_tool.py", "status"],
    "instagram": ["python", "scripts/instagram_tool.py", "accounts"],
    "viber":     ["python", "scripts/viber_tool.py", "accounts"],
    "bluesky":   ["python", "scripts/bluesky_tool.py", "status"],
}
BAD = ("401", "revoked", "expired", "not_verified", "unauthorized", "fail")


def run(name: str, cmd: list[str]) -> tuple[str, str]:
    full = ["docker", "compose", "exec", "-T", "social-api", *cmd]
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=90)
        out = (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        out = str(e)
    flag = "BAD" if any(b in out.lower() for b in BAD) else "ok"
    return flag, out


def main() -> int:
    as_json = "--json" in sys.argv
    rows = {}
    bad = 0
    for name, cmd in TOOLS.items():
        flag, out = run(name, cmd)
        rows[name] = {"status": flag, "out": out[:2000]}
        if flag == "BAD":
            bad += 1
        if not as_json:
            tail = out.strip().splitlines()[-3:] or ["(no output)"]
            print(f"\n== {name} [{flag}] ==")
            for l in tail:  # noqa: E741
                print("  " + l[:160])
    if as_json:
        print(json.dumps(rows, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
