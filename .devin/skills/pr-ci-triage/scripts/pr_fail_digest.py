#!/usr/bin/env python3
"""pr_fail_digest — compact digest of failing CI checks on a PR.

For each failing check run: pulls the failed-job log, extracts FAILED
test node IDs and the first assertion/error line per failure, and prints
a fix-oriented summary. Use before touching code — most "CI broke my PR"
cases are tests asserting pre-change constants/behavior.

Usage:
    pr_fail_digest.py 2474            # one PR
    pr_fail_digest.py --all           # every open PR with failures
    pr_fail_digest.py 2474 --full     # include log tails per failure

Requires: gh CLI authenticated.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

FAILED_RE = re.compile(r"^\S+\s+Run tests\s+\S+\s+(tests/\S+::\S+ FAILED)", re.M)
ASSERT_RE = re.compile(r"(AssertionError|assert [^\n]{0,160}|E\s+\S+Error[^\n]{0,160})")


def _gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def _open_prs() -> list[int]:
    out = _gh("pr", "list", "--json", "number", "-q", ".[].number")
    return [int(x) for x in out.split()]


def _failures(pr: int) -> list[dict]:
    """Return [{name, url, job_id}] for failing checks."""
    checks = json.loads(_gh("pr", "view", str(pr), "--json", "statusCheckRollup"))
    fails = []
    for c in checks["statusCheckRollup"]:
        concl = c.get("conclusion") or ""
        if concl in ("FAILURE", "ACTION_REQUIRED", "TIMED_OUT", "STARTUP_FAILURE"):
            fails.append({
                "name": c.get("name") or c.get("context") or "?",
                "url": c.get("detailsUrl") or c.get("targetUrl") or "",
            })
    return fails


def _job_log(url: str) -> str:
    m = re.search(r"/job/(\d+)", url)
    if not m:
        return ""
    try:
        return _gh("run", "view", "--job", m.group(1), "--log-failed")
    except Exception:
        try:
            return _gh("run", "view", "--job", m.group(1), "--log")
        except Exception:
            return ""


def digest_pr(pr: int, full: bool = False) -> None:
    fails = _failures(pr)
    print(f"\n=== PR #{pr}: {len(fails)} failing check(s)")
    for f in fails:
        print(f"\n  [{f['name']}] {f['url']}")
        log = _job_log(f["url"])
        if not log:
            print("    (no log)")
            continue
        tests = sorted(set(FAILED_RE.findall(log)))
        if tests:
            print(f"    {len(tests)} failed test(s):")
            for t in tests[:25]:
                print(f"      {t}")
            # first assertion-ish lines near failures
            lines = [l for l in log.splitlines() if ASSERT_RE.search(l)]
            seen = set()
            for l in lines:
                key = l.strip()[:120]
                if key not in seen:
                    seen.add(key)
                    print(f"      >> {key[:180]}")
                if len(seen) >= (10 if full else 5):
                    break
        else:
            # non-test failure: show last meaningful log lines
            tail = [l for l in log.splitlines() if l.strip()][-15:]
            for l in tail:
                print(f"    {l[:200]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prs", nargs="*", type=int)
    ap.add_argument("--all", action="store_true", help="digest every open PR")
    ap.add_argument("--full", action="store_true", help="more assertion context")
    args = ap.parse_args()

    prs = _open_prs() if args.all else args.prs
    if not prs:
        ap.error("pass PR numbers or --all")
    rc = 0
    for pr in prs:
        try:
            digest_pr(pr, args.full)
        except Exception as e:
            print(f"\n=== PR #{pr}: ERROR {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
