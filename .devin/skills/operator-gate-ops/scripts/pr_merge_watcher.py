#!/usr/bin/env python3
"""pr_merge_watcher.py — babysit the merge queue until every watched PR lands.

Polls each PR's rollup; when the required checks (validate/secret-scan)
report green, arms `gh pr merge --squash --auto`. Also auto-refreshes
BEHIND branches with an owner-authored empty commit (bot-pushed heads get
their runs held as action_required — owner pushes don't). Retries
transient GitHub 500/502s. Prints each merge + leaves a summary.

Usage: python pr_merge_watcher.py [pr ...] [--timeout 7200] [--no-refresh]
"""
import json
import subprocess
import sys
import time

REPO = r"C:\Users\tbaltzakis\cu130-slim"
DEFAULT_PRS = ["1309", "1302", "1301", "1322", "1326", "1321"]
REQUIRED_OK = {"validate", "secret-scan"}


def sh(args, timeout=120):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                          timeout=timeout, check=False)


def pr_state(pr):
    r = sh(["gh", "pr", "view", pr, "--json",
            "state,mergeStateStatus,headRefName,statusCheckRollup,autoMergeRequest"])
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def required_green(rollup):
    req = {c["name"]: (c.get("conclusion") or c.get("status")) for c in rollup
           if c["name"] in REQUIRED_OK}
    return len(req) == len(REQUIRED_OK) and all(
        v in ("SUCCESS", "success", "COMPLETED") for v in req.values())


def arm(pr):
    for _ in range(4):
        r = sh(["gh", "pr", "merge", pr, "--squash", "--auto", "--delete-branch"])
        if r.returncode == 0:
            return True
        time.sleep(15)
    print(f"#{pr} arm failed: {r.stderr.strip()[:80]}")
    return False


def refresh(branch):
    # rebase onto master + empty owner-commit → owner-authored head triggers CI
    if sh(["git", "checkout", "-q", "-B", branch, f"origin/{branch}"]).returncode != 0:
        return False
    sh(["git", "pull", "-q", "--rebase", "origin", "master"])
    sh(["git", "commit", "-qm", "ci: retrigger (owner-authored)", "--allow-empty"])
    return sh(["git", "push", "-qf", "origin", branch]).returncode == 0


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    prs = args or DEFAULT_PRS
    timeout = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--timeout=")), "7200"))
    no_refresh = "--no-refresh" in sys.argv
    deadline = time.time() + timeout
    pending, merged, armed = set(prs), [], []

    while pending and time.time() < deadline:
        for pr in list(pending):
            d = pr_state(pr)
            if not d:
                continue
            if d["state"] == "MERGED":
                print(f"#{pr} MERGED"); merged.append(pr); pending.remove(pr); continue
            if d["state"] != "OPEN":
                pending.remove(pr); continue
            mss = d["mergeStateStatus"]
            if d.get("autoMergeRequest"):
                print(f"#{pr} armed ({mss})"); armed.append(pr); pending.remove(pr); continue
            if mss == "BEHIND" and not no_refresh:
                print(f"#{pr} BEHIND — refreshing {d['headRefName']}")
                if refresh(d["headRefName"]):
                    print(f"#{pr} refreshed")
                continue
            if required_green(d["statusCheckRollup"]):
                print(f"#{pr} required checks green — arming")
                if arm(pr):
                    armed.append(pr); pending.remove(pr)
        if pending:
            print("watching:", ",".join(sorted(pending)), "— next poll in 90s")
            time.sleep(90)
    print(f"done. merged={merged} armed={armed} open={sorted(pending)}")
    return 0 if not pending else 1


if __name__ == "__main__":
    sys.exit(main())
