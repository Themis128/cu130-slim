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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
DOCKER = (
    "/mnt/c/Users/tbaltzakis/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"
    if Path(
        "/mnt/c/Users/tbaltzakis/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"
    ).exists()
    else "docker"
)

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
    full = [DOCKER, "compose", "exec", "-T", "social-api", *cmd]
    try:
        r = subprocess.run(
            full, capture_output=True, text=True, timeout=90, cwd=REPO_ROOT
        )
        out = (r.stdout + r.stderr).strip()
        # Non-zero exit or daemon/exec failure is a failed probe, not "ok".
        if r.returncode != 0 or not out:
            flag = "BAD"
        else:
            flag = "BAD" if any(b in out.lower() for b in BAD) else "ok"
    except Exception as e:  # noqa: BLE001
        return "BAD", str(e)
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
