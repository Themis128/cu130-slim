#!/usr/bin/env python3
"""janitor.py — host-side runner for the SocialAuto ops janitor.

Pipes janitor_payload.py into the running social-api container and runs it
against the live DB. All commands are dry-run by default; pass --apply to write.

Usage:
  janitor.py media-orphans [--pattern REGEX] [--older-than-hours H] [--apply]
  janitor.py queue-status
  janitor.py queue-requeue <post_id_prefix> [--delay-minutes M] [--apply]
  janitor.py fixtures [--apply]
  janitor.py accounts
  janitor.py sweep [--apply]   # fixtures + media-orphans + queue-status + accounts

Run from anywhere; requires the cu130-slim compose stack up (social-api container).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parent / "janitor_payload.py"
CONTAINER = "social-api"


def run(args: list[str]) -> int:
    payload = PAYLOAD.read_text()
    proc = subprocess.run(
        ["docker", "exec", "-i", "-w", "/app", CONTAINER, "python", "-", *args],
        input=payload, capture_output=True, text=True, timeout=300,
    )
    out = proc.stdout + proc.stderr
    # strip SQLAlchemy engine noise
    lines = [l for l in out.splitlines() if "sqlalchemy.engine" not in l]
    print("\n".join(lines))
    return proc.returncode


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "help":
        print(__doc__)
        return 0
    if args[0] == "sweep":
        apply_ = "--apply" in args
        extra = ["--apply"] if apply_ else []
        rc = 0
        for cmd in (["fixtures"] + extra, ["media-orphans", "--pattern", "branded-"] + extra,
                    ["queue-status"], ["accounts"]):
            print(f"\n=== janitor {' '.join(cmd)} ===")
            rc |= run(cmd)
        return rc
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
