#!/usr/bin/env python3
"""pr_merge_when_green.py — land open PRs the instant they become mergeable.

GitHub auto-merge covers the happy path, but on strict-protected master the
"treadmill" (every merge re-invalidates sibling PRs) plus stale BLOCKED
states can leave green PRs unmerged for hours. This babysitter closes that
gap: it polls open PRs and merges each one the moment its mergeStateStatus
is CLEAN, updates BEHIND branches, and reports DIRTY/BLOCKED ones for the
strategy ladder in SKILL.md.

Safe by construction:
- Only merges when GitHub itself reports CLEAN (all required checks green
  AND branch up-to-date) — never overrides protection.
- One merge per poll cycle, then re-queries: every merge invalidates the
  others, so state is always fresh.
- BEHIND branches get update-branch (422 is treated as no-op).
- DIRTY/BLOCKED/UNKNOWN are reported, never force-merged.

Usage:
  python3 pr_merge_when_green.py                  # babysit until no open PRs
  python3 pr_merge_when_green.py --once           # single sweep, then exit
  python3 pr_merge_when_green.py --interval 60 --timeout 7200
  python3 pr_merge_when_green.py --repo OWNER/REPO
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout


def gh_json(*args: str):
    out = gh(*args)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def open_prs(repo: str) -> list[dict]:
    return gh_json(
        "pr", "list", "--repo", repo, "--state", "open", "--limit", "50",
        "--json", "number,mergeStateStatus,headRefName,title",
    )


def merge_pr(repo: str, number: int) -> bool:
    out = gh(
        "pr", "merge", str(number), "--repo", repo,
        "--squash", "--delete-branch",
    )
    ok = "merged" in out.lower() or "already" in out.lower()
    print(f"  merge #{number}: {'OK' if ok else out.strip()[:200]}", flush=True)
    return ok


def update_branch(repo: str, number: int) -> None:
    out = gh(
        "api", "-X", "PUT",
        f"repos/{repo}/pulls/{number}/update-branch",
    )
    print(f"  update-branch #{number}: {out.strip()[:120] or 'queued'}", flush=True)


def sweep(repo: str) -> tuple[int, int]:
    """One pass. Returns (merged, remaining)."""
    prs = open_prs(repo)
    merged = 0
    for pr in prs:
        n, state, head = pr["number"], pr.get("mergeStateStatus", "UNKNOWN"), pr.get("headRefName", "?")
        if state == "CLEAN":
            print(f"#{n} {head}: CLEAN -> merging", flush=True)
            if merge_pr(repo, n):
                merged += 1
                break  # state changed; re-query next cycle
        elif state == "BEHIND":
            print(f"#{n} {head}: BEHIND -> update-branch", flush=True)
            update_branch(repo, n)
        elif state == "DIRTY":
            print(f"#{n} {head}: DIRTY -> needs strategy ladder (see SKILL.md)", flush=True)
        else:
            print(f"#{n} {head}: {state}", flush=True)
    return merged, len(open_prs(repo))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Themis128/cu130-slim")
    ap.add_argument("--interval", type=int, default=120, help="seconds between sweeps")
    ap.add_argument("--timeout", type=int, default=7200, help="max seconds to babysit")
    ap.add_argument("--once", action="store_true", help="single sweep then exit")
    args = ap.parse_args()

    deadline = time.time() + args.timeout
    while True:
        merged, remaining = sweep(args.repo)
        if remaining == 0:
            print("no open PRs — done", flush=True)
            return 0
        if args.once:
            return 0 if merged else 1
        if time.time() > deadline:
            print(f"timeout with {remaining} PR(s) still open", flush=True)
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
