#!/usr/bin/env python3
"""branch_consolidator.py — merge all branches with unique content into
the production branch via PRs.

Does the whole wave cycle by hand that GH won't do for you:
  1. Discover remote branches whose commits' patches are NOT in base
     (git cherry = patch-equivalence, not SHA; squash merges fool
     commit-count checks).
  2. Create a PR per branch (skips branches with zero unique patches).
  3. Approve `action_required` workflow runs (bot pushes need approval).
  4. Wave-merge: strict "up-to-date" protection means each landed PR
     re-behinds the rest — every round: merge master into each open PR
     branch, push a retrigger commit (API update-branch does NOT fire
     required checks — pushes do), wait for checks, auto-merge lands it.
  5. Resolve merge conflicts only when asked — default: report them.

    branch_consolidator.py [--base master] [--repo owner/repo] [--waves N]

Requires: gh CLI auth, git fetch perms. Runs from repo root.
"""
import json
import subprocess
import sys
import time


def sh(*args, check=True) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: {r.stderr[:200]}")
    return r.stdout.strip()


def gh(*args) -> str:
    return sh("gh", *args)


def remote_branches(base: str) -> list[str]:
    out = sh("git", "ls-remote", "--heads", "origin")
    return [l.split("refs/heads/")[1] for l in out.splitlines()
            if f"refs/heads/{base}" not in l]


def unique_patches(branch: str, base: str) -> int:
    out = sh("git", "cherry", f"origin/{base}", f"origin/{branch}", check=False)
    return sum(1 for l in out.splitlines() if l.startswith("+"))


def create_pr(branch: str, base: str, repo: str) -> int | None:
    r = gh("pr", "create", "--base", base, "--head", branch,
           "--title", f"merge: {branch} into {base}",
           "--body", f"Branch consolidation — landing unique commits from {branch}.\n\nGenerated with [Devin](https://devin.ai)")
    try:
        return int(r.rsplit("/", 1)[1])
    except Exception:
        return None


def approve_runs(repo: str, branch: str) -> None:
    runs = gh("run", "list", "--branch", branch, "--limit", "10",
              "--json", "databaseId,conclusion")
    for r in json.loads(runs):
        if r.get("conclusion") == "action_required":
            sh("gh", "api", "-X", "POST",
               f"repos/{repo}/actions/runs/{r['databaseId']}/approve",
               check=False)


def open_prs() -> dict[int, str]:
    out = gh("pr", "list", "--json", "number,headRefName")
    return {p["number"]: p["headRefName"] for p in json.loads(out)}


def arm_auto(pr: int) -> None:
    sh("gh", "pr", "merge", str(pr), "--auto", "--squash", check=False)


def update_and_retrigger(branch: str, base: str) -> bool:
    """Merge base into the PR branch + push an empty commit so required
    checks actually run (API update-branch creates no workflow runs)."""
    wt = f"/tmp/consol-{branch.replace('/', '_')}"
    sh("git", "worktree", "add", wt, "-f", f"origin/{branch}", check=False)
    try:
        m = subprocess.run(["git", "-C", wt, "merge", f"origin/{base}",
                            "-m", "merge: master"], capture_output=True, text=True)
        if m.returncode != 0:
            print(f"  {branch}: CONFLICT — needs manual resolution")
            return False
        sh("git", "-C", wt, "commit", "--allow-empty", "-m", "ci: retrigger")
        sh("git", "-C", wt, "push", "-q", "origin", f"HEAD:{branch}")
        return True
    finally:
        sh("git", "worktree", "remove", wt, "--force", check=False)


def pr_merged(repo: str, pr: int) -> bool:
    out = gh("pr", "view", str(pr), "--json", "state", "-q", ".state", check=False)
    return out.strip() == "MERGED"


def main() -> int:
    base = "master"
    repo = "Themis128/cu130-slim"
    waves = 12
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--base":
            base = args[i + 1]
        elif a == "--repo":
            repo = args[i + 1]
        elif a == "--waves":
            waves = int(args[i + 1])

    sh("git", "fetch", "origin", "--prune", "-q")
    print(f"scanning remote branches vs origin/{base}…")
    prs: dict[int, str] = {}
    for b in remote_branches(base):
        n = unique_patches(b, base)
        print(f"  {b}: {n} unique patches")
        if n:
            pr = create_pr(b, base, repo)
            if pr:
                prs[pr] = b
                arm_auto(pr)
                approve_runs(repo, b)
    for w in range(waves):
        open_ = open_prs()
        todo = {p: b for p, b in prs.items() if p in open_}
        if not todo:
            break
        print(f"wave {w + 1}: {len(todo)} PRs open — updating heads")
        for pr, b in todo.items():
            if pr_merged(repo, pr):
                continue
            update_and_retrigger(b, base)
            approve_runs(repo, b)
            arm_auto(pr)
        print("  waiting for checks + auto-merge…")
        time.sleep(240)
    remaining = {p: b for p, b in prs.items() if not pr_merged(repo, p)}
    merged = len(prs) - len(remaining)
    print(f"merged {merged}/{len(prs)}")
    for p, b in remaining.items():
        print(f"  still open: PR{p} ({b}) — check conflicts/CI")
    return 0 if not remaining else 1


if __name__ == "__main__":
    sys.exit(main())
