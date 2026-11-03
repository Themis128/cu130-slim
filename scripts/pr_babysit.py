#!/usr/bin/env python3
"""Babysit open agent PRs: resolve auto-closed review threads, update
behind branches, arm auto-merge once checks are green.

Automates the manual loop (observed on cloudless.gr#2364, 2026-11-03):
every new head gets a fresh batch of Sentry review threads; the merge gate
requires ALL threads resolved + required checks green + branch up to date.

Per open PR it will:
  - resolve review threads whose first comment says "Resolved in <sha>"
    (Sentry self-confirmed) — real bug findings are left for an agent
  - `gh pr update-branch` when the PR is BEHIND base
  - `gh pr merge --squash --auto` when mergeable + required checks pass
  - log every action to logs/pr-babysit.log (and Slack on a merge)

It never auto-resolves unanswered bug findings, never force-pushes, and
only squashes — matching repo policy.

Usage:  python scripts/pr_babysit.py [--repo Themis128/cu130-slim] [--all-repos]
Schedule: schtasks every 15 min (see docstring at bottom for command).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "logs", "pr-babysit.log")
GH = r"C:\Users\tbaltzakis\bin\gh.exe" if os.path.exists(r"C:\Users\tbaltzakis\bin\gh.exe") else "gh"
DEFAULT_REPOS = ["Themis128/cu130-slim", "Themis128/cloudless.gr"]


def log(msg: str) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = msg
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def gh(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([GH, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def gh_json(*args: str):
    r = gh(*args)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def slack_alert(text: str) -> None:
    url = os.environ.get("SLACK_ALERTS_WEBHOOK_URL")
    if not url:
        return
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                url, data=json.dumps({"text": text}).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=10,
        )
    except Exception:
        pass


def unresolved_threads(repo: str, pr: int) -> list[dict]:
    owner, name = repo.split("/")
    q = (
        '{repository(owner:"%s",name:"%s"){pullRequest(number:%d){'
        'reviewThreads(first:50){nodes{id isResolved comments(first:1){nodes{body}}}}}}}'
        % (owner, name, pr)
    )
    data = gh_json("api", "graphql", "-f", f"query={q}")
    try:
        nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    except (TypeError, KeyError):
        return []
    return [n for n in nodes if not n["isResolved"]]


def resolve_thread(thread_id: str) -> bool:
    r = gh(
        "api", "graphql", "-f",
        f'query=mutation{{resolveReviewThread(input:{{threadId:"{thread_id}"}}){{thread{{isResolved}}}}}}',
    )
    return "true" in r.stdout.lower()


def babysit_repo(repo: str) -> None:
    prs = gh_json(
        "pr", "list", "-R", repo, "--state", "open", "--limit", "30",
        "--json", "number,mergeable,mergeStateStatus,headRefName,author",
    ) or []
    for pr in prs:
        num, state = pr["number"], pr.get("mergeStateStatus", "")
        actions: list[str] = []

        # 1. Resolve threads Sentry already marked as fixed in a commit
        for t in unresolved_threads(repo, num):
            body = ""
            if t.get("comments", {}).get("nodes"):
                body = t["comments"]["nodes"][0].get("body", "")
            if body.strip().startswith("*Resolved in") or body.strip().startswith("Resolved in"):
                if resolve_thread(t["id"]):
                    actions.append(f"resolved auto-fixed thread {t['id']}")
            # Real findings stay open for an agent — never auto-resolve them.

        # 2. Branch behind? update it
        if state == "BEHIND":
            r = gh("pr", "update-branch", str(num), "-R", repo)
            if r.returncode == 0:
                actions.append("updated branch")

        # 3. Mergeable + checks green? arm auto-merge
        if state in ("CLEAN", "UNSTABLE"):  # UNSTABLE = only non-required pending
            r = gh("pr", "merge", str(num), "-R", repo, "--squash", "--auto", "--delete-branch")
            if r.returncode == 0:
                actions.append("armed auto-merge")

        if actions:
            log(f"[{repo}#{num}] " + "; ".join(actions))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append")
    ap.add_argument("--all-repos", action="store_true")
    args = ap.parse_args()
    repos = args.repo or DEFAULT_REPOS
    for repo in repos:
        try:
            babysit_repo(repo)
        except Exception as exc:
            log(f"[{repo}] error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Register (once):
#   schtasks /create /tn "cu130-pr-babysit" /sc minute /mo 15 ^
#     /tr "pythonw C:\Users\tbaltzakis\cu130-slim\scripts\pr_babysit.py"
