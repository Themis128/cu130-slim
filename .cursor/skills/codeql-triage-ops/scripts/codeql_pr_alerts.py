#!/usr/bin/env python3
"""codeql_pr_alerts.py — triage the CodeQL PR gate on cu130-slim.

When a PR shows `CodeQL  fail`, the failure means "new alerts introduced by
this diff" — NOT the standing dependency-CVE backlog. The tricky part: the
`?pr=` filter on the code-scanning API returns the *broad associated set*
(including dep CVEs that are not the gate), so this tool separates:

  GATE  — tool=CodeQL alerts introduced by the PR diff (what the check counts)
  DEP   — dependency CVE/GHSA rows (Dependabot-class; not the CodeQL gate)
  DONE  — dismissed/fixed rows (shown for context)

For each GATE alert it prints the exact dismiss command with the correct
`dismissed_reason` vocabulary (the API 422s on "wont_fix" — it wants the
human strings).

Usage:
  codeql_pr_alerts.py <pr>                # report
  codeql_pr_alerts.py <pr> --dismiss <n> --reason "won't fix" --comment "..."
  codeql_pr_alerts.py <pr> --check        # exit 1 if any open GATE alert
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

REPO = "Themis128/cu130-slim"
VALID_REASONS = ('"false positive"', '"won\'t fix"', '"used in tests"', '"mitigated"')


def gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True,
    ).stdout


def gh_json(*args: str):
    try:
        return json.loads(gh(*args))
    except json.JSONDecodeError:
        return []


def is_dep_cve(alert: dict) -> bool:
    rid = alert.get("rule", {}).get("id", "")
    tool = alert.get("tool", {}).get("name", "")
    return rid.startswith(("CVE-", "GHSA-")) or tool == "dependabot"


def fetch_pr_alerts(repo: str, pr: int) -> list[dict]:
    # state=open alone misses dismissed rows; fetch both for full context
    out = []
    for state in ("open", "dismissed", "fixed"):
        out += gh_json(
            "api", f"repos/{repo}/code-scanning/alerts?pr={pr}&state={state}&per_page=100",
        ) or []
    return out


def show(alert: dict) -> str:
    inst = alert.get("most_recent_instance", {})
    loc = inst.get("location", {})
    path = loc.get("path", "?")
    line = loc.get("start_line", "?")
    sev = alert.get("rule", {}).get("security_severity_level") or alert.get("rule", {}).get("severity", "?")
    return (
        f"  #{alert['number']:<5} {alert['rule']['id']:<45} [{sev:<8}] "
        f"{path}:{line}\n        {alert['rule'].get('description', '')[:90]}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pr", type=int)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--check", action="store_true", help="exit 1 if open gate alerts remain")
    ap.add_argument("--dismiss", type=int, metavar="ALERT_NUM")
    ap.add_argument("--reason", help="one of: false positive | won't fix | used in tests | mitigated")
    ap.add_argument("--comment", default="")
    args = ap.parse_args()

    if args.dismiss:
        if args.reason not in VALID_REASONS:
            print(f"ERROR: --reason must be one of {VALID_REASONS}", file=sys.stderr)
            return 2
        comment = args.comment or "dismissed via codeql_pr_alerts.py"
        out = gh("api", "-X", "PATCH",
                 f"repos/{args.repo}/code-scanning/alerts/{args.dismiss}",
                 "-f", "state=dismissed",
                 "-f", f"dismissed_reason={args.reason}",
                 "-f", f"dismissed_comment={comment}")
        try:
            print(f"alert {args.dismiss}: {json.loads(out)['state']}")
            return 0
        except json.JSONDecodeError:
            print(out or "dismissal failed", file=sys.stderr)
            return 1

    alerts = fetch_pr_alerts(args.repo, args.pr)
    gate = [a for a in alerts if a["state"] == "open" and not is_dep_cve(a)]
    dep = [a for a in alerts if a["state"] == "open" and is_dep_cve(a)]
    done = [a for a in alerts if a["state"] != "open"]

    print(f"PR #{args.pr} on {args.repo}")
    print(f"  gate-relevant open alerts: {len(gate)}")
    for a in gate:
        print(show(a))
    print(f"  dependency-CVE rows (not the CodeQL gate): {len(dep)}")
    print(f"  dismissed/fixed: {len(done)}")

    if gate:
        print("\nFor each alert: FIX the diff, or dismiss if the flagged")
        print("behavior is intentional design:")
        print(f"  {sys.argv[0]} {args.pr} --dismiss <num> \\")
        print(f"      --reason \"won't fix\" --comment \"<justification + precedent>\"")
        print(f"  reasons accepted by the API: {VALID_REASONS}")
        return 1 if args.check else 0
    print("\nno gate-relevant open alerts — CodeQL check should clear")
    return 0


if __name__ == "__main__":
    sys.exit(main())
