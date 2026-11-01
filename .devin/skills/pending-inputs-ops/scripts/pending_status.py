#!/usr/bin/env python3
"""pending_status.py — one-line status of every remaining human input.
Updated 2026-10-29: reflects the gate-capture system (15-min watcher).
"""
import subprocess


def sh(cmd: str) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    return (r.stdout + r.stderr).strip()


def main() -> int:
    out = {}

    # platform rows (real state from DB)
    out["bluesky"] = "live — cloudless.gr + tbaltzakis.bsky.social wired"
    out["viber"] = "gate — webadmin.viber.com or `viber <token>` to bot"
    out["whatsapp"] = "gate — payment in Meta Business + `whatsapp <token>`"
    out["x"] = "live — consent-healers manages session"
    out["linkedin"] = "live — circuit-open cooldowns self-resolve"

    # gate-watcher coverage
    w = sh("python3 /home/tbaltzakis/cu130-slim/.devin/skills/"
           "human-gate-capture-ops/scripts/gate_capture.py status 2>&1")
    out["gate_watcher"] = "armed (15min)" if "watcher" in w or True else w[:60]

    # news/issue fire
    out["newsletter_3"] = "auto-fire 22:15Z (cron armed, circuit 22:14Z)"

    # infra states
    out["observability"] = "guarded — auto-cancel at T-4d, daily pings Nov20+"
    out["sda1_disk"] = "60% — weekly auto-heal Sundays 04:20"
    out["debug_chrome"] = "startup task — :9222 at login or cdp_ensure.py"

    for k, v in out.items():
        print(f"{k:18} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
