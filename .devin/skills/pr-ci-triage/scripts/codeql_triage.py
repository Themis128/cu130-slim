#!/usr/bin/env python3
"""codeql_triage — decode GitHub Advanced Security check-run failures on a PR.

The "CodeQL" check run from the `github-advanced-security` app is a
tool-status check, NOT the security.yml codeql job. Its failure title is
usually "N configuration(s) not found" — which is almost always noise:

  * GitHub tracks analysis *configurations* (workflow + /language:<lang>
    category) seen on the base ref, and expects the PR run to produce the
    same set. The JS extractor resolves `javascript` -> javascript-typescript
    and records both extracted languages on master; a PR that only uploads
    /language:javascript then trips "/language:typescript not found".
  * That warning alone yields a NEUTRAL conclusion (see it on docs-only
    PRs). The run only flips to FAILURE when the same summary also carries
    "New alerts ... N critical" — i.e. the PR introduced a real finding.
  * So: failure => look at the new-alerts list, fix the code (SSRF guards,
    etc.). Config noise never blocks required checks.

Usage:
    codeql_triage.py 2586            # triage GHAS check runs on a PR
    codeql_triage.py 2586 --analyses # + distinct configs on master
    codeql_triage.py --stale /language:typescript   # find stale analyses
    codeql_triage.py --stale /language:typescript --delete  # delete them

Requires: gh CLI authenticated.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

REPO = "Themis128/cu130-slim"
CONFIG_RE = re.compile(
    r"because (\d+) configuration.*?:(.*?)(?=\n\n\n|### New|See annotations|\[View all|$)",
    re.S,
)
CONFIG_LINE_RE = re.compile(r"###\s+(.+?)$|`([^`]+)`", re.M)
NEW_ALERTS_RE = re.compile(r"New alerts[^:]*:\s*((?:\s*\*\s*\d+ \w+.*)+)", re.S)
BULLET_RE = re.compile(r"\*\s*(\d+) (\w+)")


def _gh_api(path: str, *extra: str) -> str:
    r = subprocess.run(["gh", "api", path, *extra], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit(f"gh api {path}: {r.stderr.strip()[:300]}")
    return r.stdout


def _gh_api_jq(path: str, jq: str):
    out = _gh_api(path, "--jq", jq)
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def head_sha(pr: str) -> str:
    r = subprocess.run(
        ["gh", "pr", "view", pr, "--json", "headRefOid", "-q", ".headRefOid"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise SystemExit(f"gh pr view {pr}: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def ghas_runs(sha: str) -> list[dict]:
    return _gh_api_jq(
        f"repos/{REPO}/commits/{sha}/check-runs?per_page=100",
        '.check_runs[] | select(.app.slug=="github-advanced-security") | '
        "{name, conclusion, id, title:.output.title, summary:.output.summary}",
    )


def triage(pr: str, show_analyses: bool) -> int:
    sha = head_sha(pr)
    runs = ghas_runs(sha)
    if not runs:
        print(f"PR {pr} ({sha[:8]}): no github-advanced-security check runs")
        return 0
    worst = 0
    for run in runs:
        title = run.get("title") or ""
        summary = run.get("summary") or ""
        concl = run["conclusion"]
        print(f"\n## {run['name']}  [{concl}]  {title}")

        m = CONFIG_RE.search(summary)
        if m:
            missing = [
                " ".join(g for g in mm if g)
                for mm in CONFIG_LINE_RE.findall(m.group(2))
            ]
            print(f"   missing configs ({m.group(1)}):")
            for line in missing:
                print(f"     {line}")

        alerts = []
        am = NEW_ALERTS_RE.search(summary)
        if am:
            alerts = BULLET_RE.findall(am.group(1))
            print(f"   NEW ALERTS: {', '.join(f'{n} {s}' for n, s in alerts)}")

        if concl == "failure":
            worst = 1
            if alerts:
                print("   => FAILURE IS ALERT-DRIVEN — fix the new findings below")
            else:
                print("   => failure with no new alerts — config mismatch, likely noise")

    if worst:
        alerts = _gh_api_jq(
            f"repos/{REPO}/code-scanning/alerts?ref=refs/pull/{pr}/merge&tool=CodeQL&state=open",
            ".[] | {number, severity:.rule.security_severity_level, rule:.rule.id, "
            "file:.most_recent_instance.location.path, "
            "line:.most_recent_instance.location.start_line}",
        )
        print(f"\n## open CodeQL alerts on refs/pull/{pr}/merge")
        for a in alerts:
            print(f"  #{a['number']} [{a.get('severity')}] {a['rule']}  {a['file']}:{a['line']}")

    if show_analyses:
        print("\n## analysis configs on master (latest 500)")
        keys = _gh_api_jq(
            f"repos/{REPO}/code-scanning/analyses?ref=refs/heads/master&per_page=100",
            '.[] | .analysis_key + "  " + .category',
        )
        for k in sorted(set(keys)):
            print(f"  {k}")
        print("  (page deeper with &page=N if the expected config is older)")
    return worst


def stale(category: str, delete: bool) -> int:
    found = []
    for page in range(1, 16):
        rows = _gh_api_jq(
            f"repos/{REPO}/code-scanning/analyses?ref=refs/heads/master&per_page=100&page={page}",
            f'.[] | select(.category=="{category}") | {{id, created_at, key:.analysis_key}}',
        )
        found.extend(rows)
        if len(rows) == 0 and page > 1:
            break
    if not found:
        print(f"no master analyses with category {category} — config expectation is")
        print("not backed by a stored analysis (extracted-language tracking or a")
        print("deleted upload). It is noise unless the check is REQUIRED; check")
        print("whether the run is neutral on other PRs.")
        return 0
    for a in found:
        print(f"  id={a['id']}  {a['created_at']}  {a['key']}")
    if delete:
        for a in found:
            _gh_api(
                f"repos/{REPO}/code-scanning/analyses/{a['id']}?confirm_delete",
                "-X", "DELETE",
            )
            print(f"  deleted {a['id']}")
    else:
        print("  (re-run with --delete to remove stale configs)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pr", nargs="?", help="PR number to triage")
    ap.add_argument("--analyses", action="store_true", help="list configs on master")
    ap.add_argument("--stale", metavar="CATEGORY", help="find master analyses with a category")
    ap.add_argument("--delete", action="store_true", help="with --stale: delete them")
    args = ap.parse_args()
    if args.stale:
        return stale(args.stale, args.delete)
    if not args.pr:
        ap.error("pr number required (or use --stale)")
    return triage(args.pr, args.analyses)


if __name__ == "__main__":
    sys.exit(main())
