#!/usr/bin/env python3
"""Turn a dir of fetched brief texts (fetch_briefs.py) into a time series.

Prints per-day 24h metrics, platform-pulse follower counts, monetization
gate progress, playbook-night recommendations (dead-night artifact check),
and whether the AI writer produced the playbook or rules fell back.

Usage: python3 brief_metrics.py [BRIEF_DIR]   (default /tmp/briefs)
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

PLATS = ["facebook", "instagram", "tiktok", "threads", "linkedin", "twitter"]


def _i(s: str | None) -> int | None:
    return int(s.replace(",", "")) if s else None


def parse(path: Path) -> dict:
    t = path.read_text()
    r: dict = {"day": path.stem}
    for key, pat in (
        ("impr24", r"Impressions\s*\|\s*([\d,]+)"),
        ("eng24", r"Engagement\s*\|\s*([\d,]+)"),
        ("accts", r"Connected accounts\s*\|\s*(\d+)"),
        ("posts24", r"Posts created \(24h\)\s*\|\s*(\d+)"),
    ):
        m = re.search(pat, t)
        r[key] = _i(m.group(1)) if m else None
    # followers: monetization table (gate text) or pulse "has N followers"
    fol: dict[str, int] = {}
    for mm in re.finditer(
        r"^ (\w+) \| ([\d,]+) \| [^|\n]*(?:to go|ahead)", t, re.M
    ):
        fol[mm.group(1)] = int(mm.group(2).replace(",", ""))
    for fm in re.finditer(r"^ (\w+)(?: ⚠)? \|[^\n]*has (\d+)\)", t, re.M):
        fol.setdefault(fm.group(1), int(fm.group(2)))
    r["followers"] = fol
    m = re.search(r"→ (\w+) is your strongest channel", t)
    r["strongest"] = m.group(1) if m else None
    m = re.search(r"⚠ sync gaps — ([^\n]+)", t)
    r["sync_gaps"] = m.group(1).strip() if m else ""
    r["ai_writer"] = "AI writer unavailable" not in t
    pb = re.search(
        r"Tomorrow's playbook\n(.*?)(?:🤖 Agent deployment|Rule-based rec|"
        r"AI-written|Data gaps|Growth initiatives|Sources:|Charts)",
        t, re.S,
    )
    r["playbook"] = pb.group(1).strip() if pb else ""
    return r


def main() -> int:
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/briefs")
    rows = [parse(f) for f in sorted(d.glob("*.txt"))]
    if not rows:
        print(f"no briefs in {d}")
        return 1
    print(f"{'day':12}{'impr24':>8}{'eng24':>7}{'posts':>6}{'acct':>5}"
          f"  ai   strongest   sync gaps")
    for r in rows:
        ai = "ai" if r["ai_writer"] else "RULE"
        print(f"{r['day']:12}{str(r['impr24']):>8}{str(r['eng24']):>7}"
              f"{str(r['posts24']):>6}{str(r['accts']):>5}  {ai:4} "
              f"{str(r['strongest']):11} {r['sync_gaps'][:55]}")
    print(f"\n{'day':12}" + "".join(f"{p:>10}" for p in PLATS))
    for r in rows:
        print(f"{r['day']:12}" + "".join(
            f"{str(r['followers'].get(p, '')):>10}" for p in PLATS))
    # dead-night check: times recommended inside the playbook section only
    print("\nDead-night playbook recs (00:00–05:59 Athens):")
    for r in rows:
        night = [f"{h}:{m}" for h, m in
                 re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", r["playbook"])
                 if int(h) < 6]
        if night:
            print(f"  {r['day']}: {', '.join(night)}")
    # recurring gap signatures
    sigs = Counter()
    for r in rows:
        for s in re.findall(r"(\w+ stats HTTP \d+|quota_exhausted)", r["sync_gaps"]):
            sigs[s] += 1
    if sigs:
        print("\nSync-gap frequency:")
        for k, v in sigs.most_common():
            print(f"  {k}: {v}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
