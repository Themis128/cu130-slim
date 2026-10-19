"""Fail CI when backend line or branch coverage drops below its gate.

pytest-cov's ``--cov-fail-under`` checks one number, and once branch
coverage is on that number is the blended line+branch percentage. This
script reads the ``coverage.json`` report and checks lines and branches
against separate floors, so a fall in either one is caught.

Usage: python scripts/check_coverage_gates.py coverage.json --lines 99.8 --branches 95.8
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def evaluate(totals: dict, min_lines: float, min_branches: float) -> tuple[bool, str]:
    statements = totals["num_statements"]
    branches = totals.get("num_branches", 0)
    if not branches:
        return False, "no branch data in report: run pytest with --cov-branch"
    line_pct = 100.0 * totals["covered_lines"] / statements if statements else 100.0
    branch_pct = 100.0 * totals["covered_branches"] / branches
    ok = line_pct >= min_lines and branch_pct >= min_branches
    msg = (
        f"line coverage {line_pct:.2f}% (gate {min_lines}%) - "
        f"branch coverage {branch_pct:.2f}% (gate {min_branches}%) - "
        f"{'OK' if ok else 'FAIL'}"
    )
    return ok, msg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--lines", type=float, required=True)
    parser.add_argument("--branches", type=float, required=True)
    args = parser.parse_args(argv)
    totals = json.loads(args.report.read_text())["totals"]
    ok, msg = evaluate(totals, args.lines, args.branches)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
