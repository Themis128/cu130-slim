#!/usr/bin/env python3
"""git_auto_push — watch a repo and auto commit+push every N changed files.

Strategy (researched against gitwatch, supsrc, watch-and-commit, and
documented auto-committer incident reports):

* Poll `git status --porcelain`
when >= --threshold files are dirty,
  stage + commit + push. Polling gives natural debounce (bursts of saves
  land in one pass instead of one commit per save).
* Protected branches (main/master) are never committed on — the watcher
  redirects to the `auto/wip` branch (created or reused) so work still
  syncs without fighting branch protection (GH006) locally.
* Safety gates before staging: refuse .env*/keys/pem paths, skip while a
  merge/rebase/cherry-pick is in progress, skip on detached HEAD,
  no-op when the index is unchanged (wac-style).
* NEVER bypasses hooks (no --no-verify, no core.hooksPath tricks) and
  NEVER force-pushes.
* Failure backoff keyed on the diff fingerprint: the documented failure
  mode is a retry storm where a linter/hook rejects the same diff every
  pass — exponential backoff (2m -> 30m cap) while the fingerprint is
  unchanged, hard-stop after --max-failures identical failures until the
  working tree actually changes.

Daemon mode runs under a systemd user service
--once is used by the
paired systemd timer for cron-like invocations.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROTECTED_BRANCHES = {"main", "master"}
AUTO_BRANCH = "auto/wip"
STATE_FILE = "auto-push-state.json"
LOCK_FILE = "auto-push.lock"

# Paths that must never be auto-staged even when tracked or untracked —
# defense-in-depth on top of .gitignore (documented auto-commit pitfall:
# `git add -A` happily stages .env / *.pem / private keys).
SECRET_PATH_PARTS = (".env", ".pem", ".key", "id_rsa", "id_ed25519", "secrets")

BACKOFF_SCHEDULE = [120, 240, 480, 960, 1800]  # seconds, capped at 30min


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[git-auto-push {ts}] {msg}", flush=True)


def notify(ntfy_url: str | None, title: str, body: str) -> None:
    """Publish a failure notice to ntfy (self-hosted). Best-effort."""
    if not ntfy_url:
        return
    try:
        req = urllib.request.Request(
            ntfy_url,
            data=body.encode(),
            headers={"Title": title, "Priority": "3"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:  # noqa: BLE001 — notify is advisory only
        log(f"ntfy notify failed (ignored): {exc}")


def load_state(git_dir: Path) -> dict:
    try:
        return json.loads((git_dir / STATE_FILE).read_text())
    except Exception:
        return {}


def save_state(git_dir: Path, state: dict) -> None:
    (git_dir / STATE_FILE).write_text(json.dumps(state, indent=1))


def dirty_files(repo: Path) -> list[str]:
    out = git(repo, "status", "--porcelain").stdout
    files = []
    for line in out.splitlines():
        path = line[3:] if len(line) > 3 else ""
        # "old -> new" rename entries: keep the new path
        path = path.split(" -> ")[-1].strip().strip('"')
        if path:
            files.append(path)
    return files


def op_in_progress(repo: Path, git_dir: Path) -> str | None:
    for marker, name in (
        ("MERGE_HEAD", "merge"),
        ("REBASE_HEAD", "rebase"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
    ):
        if (git_dir / marker).exists():
            return name
    try:
        git(repo, "rev-parse", "--verify", "HEAD")
    except subprocess.CalledProcessError:
        return "unborn-HEAD"
    return None


def fingerprint(repo: Path) -> str:
    """Hash of the pending diff — identical failures get backed off, not retried."""
    h = hashlib.sha256()
    for args in (["diff", "HEAD", "--"], ["status", "--porcelain"]):
        h.update(git(repo, *args, check=False).stdout.encode())
    return h.hexdigest()


def is_secretish(path: str) -> bool:
    low = path.lower()
    name = os.path.basename(low)
    return any(part in low for part in SECRET_PATH_PARTS) or name.endswith(
        (".pem", ".key", ".keystore", ".p12", ".pfx")
    )


def summarize(files: list[str], max_list: int = 8) -> str:
    shown = files[:max_list]
    suffix = f" (+{len(files) - max_list} more)" if len(files) > max_list else ""
    return ", ".join(shown) + suffix


def run_pass(repo: Path, args: argparse.Namespace) -> int:
    """One watch pass. Returns 0 on ok/no-op, 1 on handled failure."""
    git_dir = Path(git(repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    state = load_state(git_dir)

    # 1. Structural pre-flight
    op = op_in_progress(repo, git_dir)
    if op:
        log(f"skip — {op} in progress")
        return 0

    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch.returncode != 0:
        log("skip — detached HEAD")
        notify(args.ntfy, "git-auto-push: detached HEAD", f"{repo} is detached; auto-push paused.")
        return 0
    branch = branch.stdout.strip()

    files = dirty_files(repo)
    if len(files) < args.threshold:
        return 0

    fp = fingerprint(repo)
    now = time.time()
    if state.get("fingerprint") == fp:
        fails = state.get("fail_count", 0)
        if fails >= args.max_failures:
            return 0  # hard-stopped until the tree actually changes
        if now < state.get("next_retry", 0):
            return 0  # still in backoff

    # 2. Secret-path guard (before anything is staged)
    leaked = [f for f in files if is_secretish(f)]
    if leaked:
        log(f"REFUSE — secret-looking paths dirty: {leaked}")
        notify(
            args.ntfy,
            "git-auto-push: secret path refused",
            f"{repo}: refusing to auto-commit {leaked}. Handle manually.",
        )
        state.update({"fingerprint": fp, "fail_count": args.max_failures})
        save_state(git_dir, state)
        return 1

    # 3. Protected-branch redirect — never commit on main/master
    if branch in PROTECTED_BRANCHES:
        log(f"on protected '{branch}' — switching to {AUTO_BRANCH}")
        git(repo, "switch", "-C", AUTO_BRANCH)
        branch = AUTO_BRANCH

    # 4. Stage + commit (hooks run normally — never --no-verify)
    if args.dry_run:
        log(f"DRY-RUN would commit {len(files)} files on {branch}: {summarize(files)}")
        return 0

    git(repo, "add", "-A")
    staged = git(repo, "diff", "--cached", "--name-only").stdout.strip()
    if not staged:
        log("no-op — index unchanged after add (files reverted to HEAD content)")
        return 0

    msg = (
        f"auto: {len(files)} file{'s' if len(files) != 1 else ''} changed — "
        f"{summarize(files)}\n\n[git-auto-push]"
    )
    commit = git(repo, "commit", "-m", msg, check=False)
    if commit.returncode != 0:
        # Pre-commit hook failure (lint/type/secret-scan). Back off on the
        # fingerprint so we don't retry the identical diff every pass.
        log(f"commit rejected (hook): {commit.stderr.strip()[:300]}")
        fails = state.get("fail_count", 0) + 1
        backoff = BACKOFF_SCHEDULE[min(fails - 1, len(BACKOFF_SCHEDULE) - 1)]
        state.update(
            {"fingerprint": fp, "fail_count": fails, "next_retry": now + backoff}
        )
        save_state(git_dir, state)
        notify(
            args.ntfy,
            "git-auto-push: commit hook failure",
            f"{repo} on {branch}: {commit.stderr.strip()[:400]}\n"
            f"retry {fails}/{args.max_failures} in {backoff}s",
        )
        return 1

    head = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    log(f"committed {head} on {branch}: {summarize(files, 4)}")

    # 5. Push with one rebase retry on non-fast-forward — never force.
    push = git(repo, "push", "-u", "origin", branch, check=False)
    if push.returncode != 0:
        log("push rejected — fetch + rebase then one retry")
        git(repo, "fetch", "origin", check=False)
        rb = git(repo, "rebase", f"origin/{branch}", check=False)
        if rb.returncode != 0:
            git(repo, "rebase", "--abort", check=False)
            log(f"rebase conflict — aborted, manual resolve needed: {rb.stderr.strip()[:200]}")
            notify(
                args.ntfy,
                "git-auto-push: rebase conflict",
                f"{repo} on {branch}: commit {head} pushed-locally-only; "
                "remote moved and rebase conflicts. Resolve manually.",
            )
            return 1
        push = git(repo, "push", "-u", "origin", branch, check=False)
        if push.returncode != 0:
            log(f"push failed after rebase: {push.stderr.strip()[:300]}")
            notify(
                args.ntfy,
                "git-auto-push: push failed",
                f"{repo} on {branch}: {push.stderr.strip()[:400]}",
            )
            return 1

    log(f"pushed {head} -> origin/{branch}")
    save_state(git_dir, {"fingerprint": "", "fail_count": 0})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", required=True, type=Path, help="repo to watch")
    ap.add_argument("--threshold", type=int, default=3, help="dirty files that trigger a commit (default 3)")
    ap.add_argument("--interval", type=int, default=60, help="poll seconds in daemon mode (default 60)")
    ap.add_argument("--max-failures", type=int, default=3, help="identical-diff failures before hard-stop (default 3)")
    ap.add_argument("--ntfy", default=os.environ.get("GIT_AUTO_PUSH_NTFY", ""), help="ntfy publish URL (optional)")
    ap.add_argument("--once", action="store_true", help="single pass (timer mode)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = args.path.resolve()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}", file=sys.stderr)
        return 2

    lock_path = repo / ".git" / LOCK_FILE
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("another instance holds the lock — exiting")
            return 0

        if args.once:
            return run_pass(repo, args)
        log(f"watching {repo} (threshold={args.threshold}, interval={args.interval}s)")
        while True:
            try:
                run_pass(repo, args)
            except Exception as exc:  # noqa: BLE001 — keep the daemon alive
                log(f"pass error (continuing): {exc}")
            time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
