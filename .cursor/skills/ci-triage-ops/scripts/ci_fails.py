#!/usr/bin/env python3
"""ci_fails.py — extract FAILED lines from a PR's failing required checks.

The manual loop this replaces (run ~4x per sweep):
    gh pr checks <N>                       # find the failing job
    gh run view --job <jobid> --log-failed # hunt the FAILED lines

Usage:
    ci_fails.py <pr>            # FAILED lines from each failing required check
    ci_fails.py <pr> --healer   # is the gemini-ci-fix lane alive or quota-dead?
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

REPO = "Themis128/cu130-slim"
REQUIRED = {"validate", "secret-scan", "ruff", "test", "integration-test"}


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True).stdout


def cmd_fails(pr: str) -> int:
    checks = json.loads(gh("pr", "checks", pr, "--json", "name,state,link") or "[]")
    failing = [
        c for c in checks
        if c["state"] in ("FAILURE", "ERROR") and c["name"] in REQUIRED
    ]
    if not failing:
        print(f"#{pr}: no failing required checks")
        return 0
    for c in failing:
        m = re.search(r"/runs/(\d+)/job/(\d+)", c["link"])
        job = m.group(2) if m else None
        print(f"=== #{pr} :: {c['name']} (job {job}) ===")
        if not job:
            continue
        log = gh("run", "view", "--job", job, "--log-failed")
        fails = [ln for ln in log.splitlines() if re.search(r"FAILED |##\[error\]", ln)]
        # dedupe pytest's double-print (progress line + summary line)
        seen, out = set(), []
        for ln in fails:
            key = re.sub(r"^\S+\s+\S+\s+\S+Z\s*", "", ln)
            if key not in seen:
                seen.add(key)
                out.append(ln.strip())
        for ln in out[-15:]:
            print(ln)
    return 1


def cmd_healer() -> int:
    runs = json.loads(
        gh("run", "list", "--workflow", "gemini-ci-fix.yml", "--limit", "5",
           "--json", "conclusion,headBranch,createdAt") or "[]"
    )
    quota = any(
        "TerminalQuotaError" in b
        for pr in json.loads(gh("pr", "list", "--json", "number") or "[]")
        for b in [gh("pr", "view", str(pr["number"]), "--json", "comments", "--jq", ".comments[].body")]
    )
    state = "QUOTA-DEAD" if quota else ("healthy" if any(r["conclusion"] == "success" for r in runs) else "idle")
    print(f"gemini-ci-fix: {state}")
    for r in runs:
        print(f"  {r['conclusion']:8} {r['headBranch'][:40]:42} {r['createdAt'][:16]}")
    if quota:
        print("TerminalQuotaError seen in open-PR comments — manual fixes required until quota resets")
    return 0 if state != "QUOTA-DEAD" else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--healer":
        sys.exit(cmd_healer())
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    if len(sys.argv) > 2 and sys.argv[2] == "--healer":
        sys.exit(cmd_healer())
    sys.exit(cmd_fails(sys.argv[1]))
