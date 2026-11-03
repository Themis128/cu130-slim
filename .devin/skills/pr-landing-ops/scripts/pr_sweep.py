#!/usr/bin/env python3
"""Autonomous PR-landing sweep — the exact worktree-rebase pattern a human
session runs, unattended on a schedule. For every open PR on cu130-slim:
worktree-detach -> rebase onto master -> auto-resolve conflicts -> force-push
-> arm squash auto-merge. Runs as a cron/systemd beat on the dev box or the
omv runner; surfaces a report line per PR.
Requires `gh` + a writable repo checkout (CU130 env or cwd).
"""
import os
import shutil
import subprocess
import sys

REPO = os.environ.get("REPO", "/home/tbaltzakis/cu130-slim")
GH_REPO = "Themis128/cu130-slim"


def _sh(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=cwd or REPO, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def sweep() -> None:
    _, out = _sh(["gh", "pr", "list", "--repo", GH_REPO, "--state", "open",
                  "--limit", "20", "--json", "number,headRefName", "--jq",
                  '.[] | "\\(.number) \\(.headRefName)"'])
    for line in out.splitlines():
        num, br = line.split(maxsplit=1)
        wt = f"/tmp/wt-pr{num}"
        _sh(["git", "worktree", "add", wt, f"origin/{br}", "--detach", "-q"])
        # rebase + auto-resolve conflicts to the rebase-side (ours)
        _sh(["git", "rebase", "origin/master", "-q"], cwd=wt)
        while True:
            _, st = _sh(["git", "status"], cwd=wt)
            if "both modified" not in st:
                break
            for f in (l.split(":", 1)[1].strip() for l in st.splitlines()
                      if "both modified" in l):
                _sh(["git", "checkout", "--ours", f], cwd=wt) or \
                    _sh(["git", "checkout", "--theirs", f], cwd=wt)
                _sh(["git", "add", f], cwd=wt)
            _sh(["git", "-c", "core.editor=true", "rebase", "--continue"], cwd=wt)
            if "rebase in progress" not in _sh(["git", "status"], cwd=wt)[1]:
                break
        rc, res = _sh(["git", "push", "-f", "origin", f"HEAD:{br}"], cwd=wt)
        if rc == 0:
            _sh(["gh", "pr", "merge", num, "--repo", GH_REPO, "--auto",
                 "--squash"])
            print(f"{num}: armed")
        else:
            print(f"{num}: STUCK")
        _sh(["git", "worktree", "remove", wt, "-f"])
    shutil.rmtree("/tmp/wt-pr*", ignore_errors=True) if False else None


if __name__ == "__main__":
    sweep()
