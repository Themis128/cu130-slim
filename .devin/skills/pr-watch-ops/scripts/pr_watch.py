#!/usr/bin/env python3
"""pr_watch.py — sweep open PRs, classify red checks, fix what's mechanical.

For every open PR: collect check status, classify each failure against the
known-noise signature table (advisory scans, stale-workflow runs, dep-CVE
noise), and — with flags — approve action_required runs, update BEHIND
branches, and arm auto-merge.

Output is one line per PR plus an ACTIONABLE list containing only failures
that need a human/agent to read a log — advisory noise is grouped, not repeated.

Usage:
  python pr_watch.py                 # report only
  python pr_watch.py --fix           # approve + update-branch + arm auto-merge
  python pr_watch.py --repo OWNER/REPO
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

REPO = "Themis128/cu130-slim"
MASTER = "master"

# Check-name signatures -> classification. Anything not matching is ACTIONABLE.
ADVISORY_PATTERNS = (
    "CodeQL",            # dependency-CVE + config-drift alerts; never a gate
    "Cursor Approval",   # informational reviewer bots
    "SonarCloud",
    "codecov",
)

SCAN_PATTERNS = (
    "Trivy",
    "container-scan",
    "Generate SBOM",
)


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


def classify(check: dict) -> str:
    name = check.get("name", "")
    for pat in ADVISORY_PATTERNS:
        if pat in name:
            return "advisory"
    for pat in SCAN_PATTERNS:
        if pat in name:
            return "scan"
    return "actionable"


def run_ids_for_branch(branch: str, status: str) -> list[int]:
    runs = gh_json(
        "run", "list", "--branch", branch, "--limit", "10",
        "--json", "databaseId,conclusion",
    )
    return [r["databaseId"] for r in runs if r.get("conclusion") == status]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="approve action_required, update BEHIND, arm auto-merge")
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args()

    prs = gh_json(
        "pr", "list", "--repo", args.repo,
        "--json", "number,title,headRefName,mergeStateStatus,autoMergeRequest",
    )
    if not prs:
        print("no open PRs")
        return 0

    actionable, notes = [], []

    for pr in prs:
        n, branch, state = pr["number"], pr["headRefName"], pr["mergeStateStatus"]
        auto = bool(pr.get("autoMergeRequest"))
        checks = gh("pr", "checks", str(n), "--repo", args.repo)
        counts = {"pass": 0, "pending": 0, "fail": 0, "skipping": 0}
        fails = []
        for line in checks.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name, result = parts[0], parts[1]
            counts[result if result in counts else "pending"] += 1
            if result == "fail":
                fails.append((name, classify({"name": name})))

        kinds = {k for _, k in fails}
        tag = ""
        if "actionable" in kinds:
            tag = " **ACTIONABLE**"
            actionable += [f"#{n} {name}" for name, k in fails if k == "actionable"]
        elif fails:
            tag = " (advisory/scan noise)"

        print(
            f"#{n} {state} auto={auto} "
            f"pass:{counts['pass']} pend:{counts['pending']} fail:{counts['fail']}{tag}  "
            f"{pr['title'][:55]}"
        )
        for name, k in fails:
            print(f"    {k:>10}  {name}")

        if not args.fix:
            continue

        # approve action_required runs (heal-bot + first-time gate)
        for rid in run_ids_for_branch(branch, "action_required"):
            gh("api", "-X", "POST", f"repos/{args.repo}/actions/runs/{rid}/approve")
            notes.append(f"#{n}: approved run {rid}")

        # BEHIND -> update-branch
        if state == "BEHIND":
            msg = gh("api", "-X", "PUT", f"repos/{args.repo}/pulls/{n}/update-branch").strip()
            notes.append(f"#{n}: update-branch -> {msg[:60]}")

        # arm auto-merge
        if not auto:
            gh("pr", "merge", str(n), "--repo", args.repo, "--squash", "--delete-branch", "--auto")
            notes.append(f"#{n}: armed auto-merge")

    print()

    # Suspicious empty merges: a PR closed as MERGED with zero changed files
    # usually means the head branch was rebuilt onto the base and the real
    # commits were silently dropped (seen 2026-11-09 with #1543). Flag any
    # such merge from the last 48h so it can't pass unnoticed.
    merged = gh_json(
        "pr", "list", "--repo", args.repo, "--state", "merged", "--limit", "30",
        "--json", "number,title,additions,deletions,changedFiles,mergedAt",
    )
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    for m in merged:
        try:
            when = datetime.fromisoformat(m["mergedAt"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if m.get("changedFiles") == 0 and when >= cutoff:
            print(f"EMPTY-MERGE #{m['number']} {m['title'][:60]} — verify the work actually landed")
            actionable.append(f"#{m['number']} merged with 0 changed files (possible rebuild wipe)")

    if notes:
        print("actions taken:")
        for note in notes:
            print(f"  {note}")
    if actionable:
        print("ACTIONABLE failures (need log review):")
        for a in actionable:
            print(f"  {a}")
        return 1
    print("no actionable failures — all red is advisory/scan noise or already-fixed classes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
