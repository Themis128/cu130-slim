#!/usr/bin/env python3
"""pr_sweep_all.py — one-command "address all PRs → merge to master → rebase".

Orchestrates the existing PR tooling in the safe order:

  1. pr_watch.py --fix      approve action_required runs, update-branch on
                            BEHIND, arm squash auto-merge on everything open.
  2. pr_rebuild.py --close  for PRs still DIRTY (real conflicts): API-only
                            conflict-free rebuild onto master + supersede.
  3. report                 re-list open PRs; anything still red with
                            actionable (non-advisory) check failures is
                            printed for log review — exit 1.

It deliberately does NOT blind-resolve rebase conflicts (the old
pr-landing-ops pr_sweep.py took --ours/--theirs blindly). Judgment calls
are reported, not guessed.

Usage:
  python3 pr_sweep_all.py                  # full sweep (watch --fix + rebuild dirty)
  python3 pr_sweep_all.py --dry-run        # report only, no mutations
  python3 pr_sweep_all.py --no-rebuild     # skip the DIRTY rebuild phase
  python3 pr_sweep_all.py --repo OWNER/REPO
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(HERE, "..", "..", "pr-watch-ops", "scripts", "pr_watch.py")
REBUILD = os.path.join(
    HERE, "..", "..", "pr-heal-ops", "scripts", "pr_rebuild.py"
)
REPO = "Themis128/cu130-slim"


def run(cmd: list[str], check: bool = False) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if check and r.returncode != 0:
        print(f"  ! {' '.join(cmd)} -> {out[-300:]}", file=sys.stderr)
    return r.returncode, out


def open_prs(repo: str) -> list[dict]:
    _, out = run([
        "gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", "50",
        "--json", "number,title,headRefName,mergeStateStatus,autoMergeRequest",
    ])
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only")
    ap.add_argument("--no-rebuild", action="store_true", help="skip DIRTY rebuilds")
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args()

    prs = open_prs(args.repo)
    if not prs:
        print("no open PRs — nothing to sweep")
        return 0
    print(f"{len(prs)} open PR(s) on {args.repo}\n")

    # Phase 1 — mechanical unblock (approve / update-branch / arm auto-merge)
    if args.dry_run:
        print("== phase 1: pr_watch report (dry-run) ==")
        _, out = run(["python3", WATCH, "--repo", args.repo])
        print(out)
    else:
        print("== phase 1: pr_watch --fix ==")
        _, out = run(["python3", WATCH, "--fix", "--repo", args.repo])
        print(out)

    # Phase 2 — conflict-free rebuilds for DIRTY PRs
    if not args.no_rebuild and not args.dry_run:
        dirty = [p for p in open_prs(args.repo)
                 if p.get("mergeStateStatus") == "DIRTY"]
        if dirty:
            print(f"\n== phase 2: rebuild {len(dirty)} DIRTY PR(s) ==")
            for p in dirty:
                n = p["number"]
                print(f"  rebuilding #{n} {p['title'][:50]}")
                rc, out = run(["python3", REBUILD, str(n), "--close"], check=True)
                print(f"    {out.splitlines()[-1] if out else '(no output)'}")
        else:
            print("\n== phase 2: no DIRTY PRs ==")

    # Phase 3 — final state + actionable failures
    print("\n== final state ==")
    rc, out = run(["python3", WATCH, "--repo", args.repo])
    print(out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
