#!/usr/bin/env python3
"""Backtick-safe PR create/edit + auto-merge for cu130-slim.

Workarounds baked in for two recurring failures:
- `gh pr create/edit --body` mangles backticks when the command passes
  through nested shells (Git Bash -> wsl.exe -> bash -c). This tool always
  sends the body via REST PATCH with a JSON payload built by python —
  no shell interpolation of the content.
- `gh pr merge --auto` can die on the Projects-classic GraphQL
  deprecation; this falls back to the enablePullRequestAutoMerge mutation.

Usage:
    gh_pr_safe.py create --title T --body-file F --head BRANCH [--base master]
    gh_pr_safe.py edit   PR --body-file F
    gh_pr_safe.py automerge PR [--method SQUASH|MERGE|REBASE]
    gh_pr_safe.py body   PR --body-file F   # alias of edit (body only)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request

REPO = "Themis128/cu130-slim"


def _token() -> str:
    out = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return out


def api(method: str, path: str, payload: dict | None = None):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as r:
        body = r.read()
        return json.loads(body) if body else {}


def graphql(query: str, variables: dict | None = None):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        method="POST",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def cmd_create(a):
    pr = api(
        "POST",
        f"/repos/{REPO}/pulls",
        {"title": a.title, "head": a.head, "base": a.base, "body": a.body},
    )
    print(pr["html_url"])
    if a.auto:
        cmd_automerge(argparse.Namespace(pr=pr["number"], method="SQUASH"))


def cmd_edit(a):
    api("PATCH", f"/repos/{REPO}/pulls/{a.pr}", {"body": a.body})
    print(f"PR #{a.pr} body updated")


def cmd_automerge(a):
    node = api("GET", f"/repos/{REPO}/pulls/{a.pr}")["node_id"]
    q = (
        "mutation($id:ID!,$m:PullRequestMergeMethod!){"
        "enablePullRequestAutoMerge(input:{pullRequestId:$id,mergeMethod:$m}){"
        "pullRequest{autoMergeRequest{enabledAt mergeMethod}}}}"
    )
    res = graphql(q, {"id": node, "m": a.method})
    am = (res.get("data") or {}).get("enablePullRequestAutoMerge")
    if am and am["pullRequest"]["autoMergeRequest"]:
        print(f"auto-merge armed on #{a.pr}: {am['pullRequest']['autoMergeRequest']}")
    else:
        print(json.dumps(res, indent=2)[:800])
        sys.exit(1)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("--title", required=True)
    c.add_argument("--body-file", required=True)
    c.add_argument("--head", required=True)
    c.add_argument("--base", default="master")
    c.add_argument("--auto", action="store_true")
    c.set_defaults(fn=cmd_create)

    e = sub.add_parser("edit")
    e.add_argument("pr", type=int)
    e.add_argument("--body-file", required=True)
    e.set_defaults(fn=cmd_edit)

    m = sub.add_parser("automerge")
    m.add_argument("pr", type=int)
    m.add_argument("--method", default="SQUASH")
    m.set_defaults(fn=cmd_automerge)

    args = p.parse_args()
    # resolve body-file -> body for create/edit
    bf = getattr(args, "body_file", None)
    if bf:
        args.body = open(bf, encoding="utf-8").read()
    args.fn(args)


if __name__ == "__main__":
    main()
