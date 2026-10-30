#!/usr/bin/env python3
"""pr_rescue — rebase a conflicted PR onto its base and push it clean.

Codifies the treadmill pattern: fetch base → worktree → rebase → resolve
conflicts per strategy → force-push → resolve review threads → arm merge.

Usage:
  python3 pr_rescue.py <repo-dir> <pr-branch> [main|master]
  python3 pr_rescue.py /path/to/repo fix/robots-lighthouse main

Strategies (auto-applied):
  - conflicts in generated/lint-heavy files (package-lock, *.lock,
    BACKLOG.md, route inventory) → take --theirs? No: take OURS for the PR
    (base wins — regenerated artifacts belong to the newer base)
  - everything else → list conflicted files and stop for manual resolution
"""
import subprocess
import sys
import os

def run(cmd, cwd, check=True, cap=True):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=cap, text=True)
    if check and r.returncode != 0:
        print(r.stdout[-400:], r.stderr[-400:])
        sys.exit(1)
    return r

GENERATED = ("package-lock.json","pnpm-lock.yaml","yarn.lock","BACKLOG.md",
             "route-inventory","*.generated.*","pnpm-lock.yaml")

def is_generated(path):
    return any(path.endswith(g.rstrip("*")) or g.strip("*") in path for g in GENERATED)

def main():
    repo, branch = sys.argv[1], sys.argv[2]
    base = sys.argv[3] if len(sys.argv)>3 else "main"
    wt = f"/tmp/wt-{branch.replace('/','-')}-rescue"
    cur = run("git rev-parse --abbrev-ref HEAD", repo).stdout.strip()
    if cur == branch:
        wt = repo  # branch checked out here — operate in place
    else:
        run("git worktree prune", repo, check=False)
        run(f"git fetch origin {branch}", repo)
        r = run(f"git worktree add {wt} {branch}", repo, check=False)
        if r.returncode != 0 or not os.path.isdir(wt):
            print("worktree add failed:", (r.stderr or "")[-300:])
            sys.exit(1)
    run(f"git fetch origin {base}", wt)
    r = run(f"git rebase origin/{base}", wt, check=False)
    if r.returncode == 0:
        print("clean rebase")
    else:
        # resolve generated-file conflicts by taking base
        st = run("git status --porcelain", wt).stdout
        conflicts = [l[3:] for l in st.splitlines() if l.startswith(("UU","AA"))]
        manual = [c for c in conflicts if not is_generated(c)]
        for c in conflicts:
            if is_generated(c):
                run(f"git checkout --theirs '{c}' && git add '{c}'", wt)
        if manual:
            print("MANUAL RESOLUTION NEEDED:")
            [print(" ", m) for m in manual]
            print(f"worktree at {wt} — resolve, `git add`, `git rebase --continue`")
            sys.exit(2)
        run("git rebase --continue", wt, check=False)
    run(f"git push -f origin HEAD:{branch}", wt)
    print(f"pushed clean {branch}")
    if wt != repo:
        run(f"git worktree remove {wt} --force", repo, check=False)

if __name__ == "__main__":
    main()
