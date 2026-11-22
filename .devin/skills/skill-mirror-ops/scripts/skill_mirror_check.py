#!/usr/bin/env python3
"""skill_mirror_check.py — audit .devin/skills vs .cursor/skills mirror drift.

Classes per skill: ok | doc-drift | script-gap | devin-only | cursor-only.
  doc-drift  — SKILL.md differs between mirrors (dangerous: stale guidance)
  script-gap — a tracked file exists only on one side (mirror incomplete)
               untracked files are skipped (live WIP — don't touch)

    skill_mirror_check.py          # report
    skill_mirror_check.py --sync   # copy missing TRACKED files both directions
"""
from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/tbaltzakis/cu130-slim")
SIDES = (".devin/skills", ".cursor/skills")
SKIP = {"__pycache__", ".pytest_cache"}
TRACKED = set(
    subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True, text=True,
    ).stdout.splitlines()
)


def files_under(d: Path) -> set[Path]:
    return {
        p.relative_to(d) for p in d.rglob("*")
        if p.is_file() and not any(part in SKIP or p.suffix == ".pyc" for part in p.parts)
    }


def audit() -> tuple[dict, dict]:
    devin, cursor = (ROOT / s for s in SIDES)
    dskills = {p.name for p in devin.iterdir() if p.is_dir()}
    cskills = {p.name for p in cursor.iterdir() if p.is_dir()}
    rows, gaps = {}, {}
    for name in sorted(dskills | cskills):
        d, c = devin / name, cursor / name
        if name not in dskills:
            rows[name] = "cursor-only"
            continue
        if name not in cskills:
            rows[name] = "devin-only"
            continue
        dfiles, cfiles = files_under(d), files_under(c)
        dd, cc = d / "SKILL.md", c / "SKILL.md"
        if dd.exists() and cc.exists() and not filecmp.cmp(dd, cc, shallow=False):
            rows[name] = "doc-drift"
            continue
        missing = sorted((dfiles - cfiles) | (cfiles - dfiles))
        tracked_gaps = [f for f in missing if any(
            str((s / name / f).relative_to(ROOT)) in TRACKED for s in (devin, cursor)
        )]
        rows[name] = "script-gap" if tracked_gaps else "ok"
        if tracked_gaps:
            gaps[name] = tracked_gaps
    return rows, gaps


def sync(gaps: dict) -> None:
    for name, files in gaps.items():
        for rel in files:
            for src_side, dst_side in ((SIDES[0], SIDES[1]), (SIDES[1], SIDES[0])):
                src = ROOT / src_side / name / rel
                dst = ROOT / dst_side / name / rel
                if src.exists() and not dst.exists() and str(src.relative_to(ROOT)) in TRACKED:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    print(f"  synced {src_side} -> {dst_side}: {name}/{rel}")


if __name__ == "__main__":
    rows, gaps = audit()
    counts = {}
    for name, cls in rows.items():
        counts[cls] = counts.get(cls, 0) + 1
        if cls not in ("ok", "devin-only", "cursor-only"):
            print(f"{cls:12} {name}")
    print("---")
    for cls in ("ok", "doc-drift", "script-gap", "devin-only", "cursor-only"):
        print(f"{cls:12} {counts.get(cls, 0)}")
    if "--sync" in sys.argv and gaps:
        print("--- syncing tracked gaps ---")
        sync(gaps)
    sys.exit(1 if any(v == "doc-drift" for v in rows.values()) else 0)
