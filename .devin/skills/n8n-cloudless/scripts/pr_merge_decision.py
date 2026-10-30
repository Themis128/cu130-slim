#!/usr/bin/env python3
"""Merge-decision evaluator for open PRs / branches.

Rules engine: evaluates each PR against the repo's merge conventions and
prints a verdict + reason. Never merges — decision support only.

Usage:
  python -X utf8 pr_merge_decision.py Themis128/cloudless.gr 2282 2284
  python -X utf8 pr_merge_decision.py --repo Themis128/cloudless.gr --open

Verdicts:
  MERGE          — safe to merge now (green checks, no blockers)
  WAIT-CI        — checks still running or failing; recheck later
  DO-NOT-MERGE   — explicit label/blocker set by owner; leave for user
  NEEDS-OWNER    — mergeable but requires owner's call (scope, secrets, prod)
"""
import json
import subprocess
import sys
import urllib.request

def token():
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    sys.exit("no github token in credential store")

def gh(repo, path):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        headers={"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json"})
    return json.load(urllib.request.urlopen(req, timeout=20))

def evaluate(repo, num):
    pr = gh(repo, f"/pulls/{num}")
    checks = gh(repo, f"/commits/{pr['head']['sha']}/check-runs?per_page=50").get("check_runs", [])
    labels = [l["name"] for l in pr.get("labels", [])]
    draft = pr.get("draft")
    state = pr.get("mergeable_state") or pr.get("mergeable")

    blockers, failing, pending = [], [], []
    for c in checks:
        if c["conclusion"] in ("failure", "cancelled", "timed_out", "action_required"):
            failing.append(c["name"])
        elif c["status"] != "completed":
            pending.append(c["name"])

    if "do-not-merge" in labels or draft:
        verdict, why = "DO-NOT-MERGE", f"explicit label/draft ({labels})"
    elif failing:
        verdict, why = "WAIT-CI", f"failing checks: {failing[:4]}"
    elif pending:
        verdict, why = "WAIT-CI", f"pending checks: {pending[:4]}"
    elif state in ("blocked", "behind", "unstable"):
        verdict, why = "WAIT-CI", f"mergeable_state={state}"
    else:
        verdict, why = "NEEDS-OWNER", "green — but merge is an owner decision (prod scope, labels, or review convention)"

    return {"num": num, "title": pr["title"], "head": pr["head"]["ref"], "labels": labels,
            "state": state, "checks": f"{len(checks)}", "verdict": verdict, "why": why}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[0] == "--repo":
        repo = args[1]
        nums = [pr["number"] for pr in gh(repo, "/pulls?state=open&per_page=30")]
        print(f"open PRs: {nums}")
    else:
        repo, nums = args[0], [int(x) for x in args[1:]]
    for n in nums:
        r = evaluate(repo, n)
        print(f"\nPR #{r['num']} [{r['verdict']}] {r['title']}")
        print(f"  head={r['head']}  state={r['state']}  checks={r['checks']}  labels={r['labels']}")
        print(f"  why: {r['why']}")
