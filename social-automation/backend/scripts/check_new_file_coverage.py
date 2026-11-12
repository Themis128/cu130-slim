"""Fail CI when a PR-added ``app/`` file has inadequate test coverage.

The global gates in ``check_coverage_gates.py`` only see repo-wide totals —
a single new module at 13% coverage just prints "99.00% FAIL" and the
culprit must be hunted in the coverage table. This script names the files:
it reads the coverage.py JSON report plus the list of files the PR added
under ``social-automation/backend/app/`` and requires each to reach
``--min-lines`` line coverage. Files absent from the report count as 0%.

Usage:
    git diff --name-only --diff-filter=A origin/master...HEAD \
        -- social-automation/backend/app/ > /tmp/new_app_files.txt
    python scripts/check_new_file_coverage.py coverage.json \
        --min-lines 80.0 /tmp/new_app_files.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_PREFIX = "social-automation/backend/"


def line_pct(entry: dict | None) -> float:
    if not entry:
        return 0.0
    summary = entry.get("summary", {})
    statements = summary.get("num_statements", 0)
    if not statements:
        return 100.0
    return 100.0 * summary.get("covered_lines", 0) / statements


def evaluate(files: dict, targets: list[str], min_lines: float) -> tuple[bool, list[str], list[str]]:
    """Return (ok, failing 'path: pct%' strings, checked paths)."""
    failures: list[str] = []
    checked: list[str] = []
    for rel in targets:
        pct = line_pct(files.get(rel))
        checked.append(rel)
        if pct < min_lines:
            failures.append(f"{rel}: {pct:.1f}% (gate {min_lines}%)")
    return not failures, failures, checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="coverage.py JSON report")
    parser.add_argument("filelist", type=Path, help="PR-added files, one per line")
    parser.add_argument("--min-lines", type=float, default=80.0)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = parser.parse_args(argv)

    files = json.loads(args.report.read_text()).get("files", {})
    targets: list[str] = []
    for raw in args.filelist.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        rel = line.removeprefix(args.prefix)
        if rel.startswith("app/") and rel.endswith(".py"):
            targets.append(rel)

    _, failures, checked = evaluate(files, targets, args.min_lines)
    if failures:
        print("PR-added app/ files below the coverage floor:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print(f"new-file coverage gate OK ({len(checked)} added app/ files >= {args.min_lines}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
