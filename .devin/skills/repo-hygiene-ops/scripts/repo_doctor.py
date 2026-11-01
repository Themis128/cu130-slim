#!/usr/bin/env python3
"""repo_doctor.py — classify every dirty path in this checkout.

Buckets each `git status` entry as:
  STALE      worktree content == origin/master (safe to restore)
  WIP        worktree differs from origin/master (real local work — keep)
  UNTRACKED-UPSTREAM  untracked file that exists on origin/master
  UNTRACKED-WIP       untracked file not on origin/master
  ROOT-OWNED          file/dir owned by root — write/unlink will fail
                      needs `sudo chown -R <user>:<user> <repo>`

Also reports: core.fileMode, CRLF-in-porcelain, stale worktrees,
and the dual-checkout warning (Docker mounts C:\\... not /home/...).

Usage: python3 repo_doctor.py [--json]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True,
    ).stdout


def main() -> None:
    repo = git("rev-parse", "--show-toplevel").strip()
    os.chdir(repo)
    git("fetch", "origin", "-q")

    fm = git("config", "core.fileMode").strip() if subprocess.run(
        ["git", "config", "core.fileMode"], capture_output=True).returncode == 0 else "unset"

    entries = []
    for line in git("status", "--porcelain", "-z").split("\0"):
        if not line.strip():
            continue
        entries.append((line[:2], line[3:].strip().rstrip("\r")))

    report: dict[str, list[str]] = {k: [] for k in
        ("STALE", "WIP", "UNTRACKED-UPSTREAM", "UNTRACKED-WIP", "ROOT-OWNED")}

    def classify_path(p: str, status: str) -> None:
        try:
            st = os.lstat(p)
            if st.st_uid == 0:
                report["ROOT-OWNED"].append(p)
                return
        except FileNotFoundError:
            pass
        if status == "??":
            try:
                git("rev-parse", f"origin/master:{p}")
                report["UNTRACKED-UPSTREAM"].append(p)
            except subprocess.CalledProcessError:
                report["UNTRACKED-WIP"].append(p)
            return
        try:
            w = git("hash-object", p).strip()
            u = git("rev-parse", f"origin/master:{p}").strip()
            report["STALE" if w == u else "WIP"].append(p)
        except subprocess.CalledProcessError:
            report["WIP"].append(p)

    for status, path in entries:
        if path.endswith("/"):  # untracked dir — expand
            for dp, _, fns in os.walk(path):
                for fn in fns:
                    classify_path(os.path.join(dp, fn), "??")
        else:
            classify_path(path, status)

    wts = [l for l in git("worktree", "list").splitlines() if "prunable" in l]  # noqa: E741

    if "--json" in sys.argv:
        print(json.dumps({"core.fileMode": fm, "paths": report,
                          "prunable_worktrees": wts}, indent=2))
        return

    print(f"repo: {repo}   core.fileMode: {fm}   (set to 'false' to kill mode noise)")
    for k, v in report.items():
        print(f"\n{k} ({len(v)})")
        for p in v[:40]:
            print(f"  {p}")
        if len(v) > 40:
            print(f"  … +{len(v)-40} more")
    if wts:
        print(f"\nPRUNABLE WORKTREES ({len(wts)}) — `git worktree prune`:")
        for l in wts:  # noqa: E741
            print(f"  {l}")
    if report["ROOT-OWNED"]:
        print("\nFIX: sudo chown -R $(id -un):$(id -gn) " + repo)
    print("\nNOTE: Docker mounts C:\\Users\\tbaltzakis\\cu130-slim — "
          "container file ops see the WINDOWS checkout, not this one.")


if __name__ == "__main__":
    main()
