#!/usr/bin/env python3
"""Static audit: find bool-only health probes in worker tasks.

Scans ``app/worker/tasks/*.py`` (and optionally the whole ``app/`` tree)
for the failure class behind the 2026-11-22 IG mass-expire incident:

  * ``except`` blocks that return ``False`` or write a dead status
    (``expired``/``revoked``) — a transport error must be inconclusive,
    never proof the credential died.
  * probe helpers annotated ``-> bool`` that swallow exceptions into a
    boolean — the tri-state contract requires ``bool | None``.
  * direct ``status = "expired"``/``"revoked"`` assignments — flag for
    review that a confirmation/re-probe or definitive rejection guards it.

This is a heuristic lint, not a proof: review each hit against the
health-probe-ops contract before changing code.

Usage:
    python3 probe_audit.py [backend_dir]     # default: ./social-automation/backend
    python3 probe_audit.py --json            # machine-readable output

Exit code: 0 always (report tool); use hits to drive review.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEAD_STATUSES = ("expired", "revoked")

# except-block → dead verdict patterns (line-level heuristics)
RE_EXCEPT_FALSE = re.compile(r"except\b[^\n]*:\s*(#.*)?$")
RE_RETURN_FALSE = re.compile(r"^\s*return\s+False\s*$")
RE_RETURN_TRUE = re.compile(r"^\s*return\s+True\s*$")
RE_STATUS_WRITE = re.compile(
    r"""\.status\s*=\s*["'](%s)["']""" % "|".join(DEAD_STATUSES)
)
RE_BOOL_ANNOTATION = re.compile(r"->\s*bool\b(?!\s*\|)")
RE_TRISTATE = re.compile(r"bool\s*\|\s*None|Optional\[bool\]")


def _line_no(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def audit_file(path: Path) -> list[dict]:
    hits: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{"file": str(path), "line": 0, "kind": "unreadable", "text": str(exc)}]

    lines = text.splitlines()
    in_except = False
    except_indent = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Track whether we're inside an except handler body.
        if RE_EXCEPT_FALSE.search(line) and not stripped.startswith("#"):
            in_except = True
            except_indent = len(line) - len(line.lstrip())
            continue
        if in_except:
            indent = len(line) - len(line.lstrip())
            if stripped and indent <= except_indent and not stripped.startswith(")"):
                in_except = False
            elif RE_RETURN_FALSE.search(line):
                hits.append(
                    {
                        "file": str(path),
                        "line": i,
                        "kind": "except-returns-False",
                        "text": stripped,
                        "severity": "high",
                    }
                )
            elif RE_STATUS_WRITE.search(line):
                hits.append(
                    {
                        "file": str(path),
                        "line": i,
                        "kind": "except-writes-dead-status",
                        "text": stripped,
                        "severity": "high",
                    }
                )

        if RE_STATUS_WRITE.search(line) and not in_except:
            hits.append(
                {
                    "file": str(path),
                    "line": i,
                    "kind": "dead-status-write",
                    "text": stripped,
                    "severity": "review",
                }
            )

    # Function-level: bool-annotated probe helpers without tri-state.
    for m in re.finditer(
        r"def\s+(\w*(?:probe|check|verify|health|session)\w*)\s*\([^)]*\)\s*->\s*bool\b(?!\s*\|)",
        text,
        re.IGNORECASE,
    ):
        # Skip if the same def line also mentions tri-state (defensive).
        hits.append(
            {
                "file": str(path),
                "line": _line_no(text, m.start()),
                "kind": "bool-only-probe-helper",
                "text": m.group(0).strip(),
                "severity": "review",
            }
        )

    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backend_dir", nargs="?", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true", help="scan all of app/ not just worker/tasks")
    args = ap.parse_args()

    here = Path(__file__).resolve()
    # scripts/ -> health-probe-ops/ -> skills/ -> .devin/ -> repo root
    repo = here.parents[4]
    backend = Path(args.backend_dir) if args.backend_dir else repo / "social-automation" / "backend"
    scan_root = backend / "app" if args.all else backend / "app" / "worker" / "tasks"
    if not scan_root.is_dir():
        print(f"probe_audit: {scan_root} not found", file=sys.stderr)
        return 2

    hits: list[dict] = []
    for py in sorted(scan_root.rglob("*.py")):
        hits.extend(audit_file(py))

    if args.json:
        print(json.dumps({"files_scanned": len(list(scan_root.rglob('*.py'))), "hits": hits}, indent=2))
        return 0

    high = [h for h in hits if h["severity"] == "high"]
    review = [h for h in hits if h["severity"] == "review"]
    print(f"probe_audit: {len(list(scan_root.rglob('*.py')))} files, "
          f"{len(high)} high / {len(review)} review hits")
    for h in hits:
        print(f"  [{h['severity']:6}] {h['file']}:{h['line']}  {h['kind']}  {h['text'][:90]}")
    if high:
        print("\nHIGH hits are the incident class — verify each is guarded by a "
              "confirmation re-probe or definitive-rejection check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
