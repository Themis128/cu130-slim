#!/usr/bin/env python3
"""harvest_contacts.py — turn a LinkedIn connections.csv (or any email list)
into a filtered, deduped survey audience.

Usage:
    harvest_contacts.py Connections.csv               # classify + paste-flags
    harvest_contacts.py Connections.csv --attach <CID> --yes
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

JUNK = re.compile(
    r"^(noreply|no-reply|notification|mailer-daemon|postmaster|support|hello|"
    r"info|contact|admin|newsletter|marketing|billing|security|register)|"
    r"@mail\.|linkedin\.|skool\.|messenger\.local$|test\.dev$|example\.",
    re.I)
OWNER = re.compile(r"baltzakis|themis|tbaltzakis|cloudless\.gr", re.I)
EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def extract(path: Path) -> list[str]:
    out = set()
    text = path.read_text(errors="replace")
    if path.suffix.lower() == ".csv":
        for row in csv.reader(text.splitlines()):
            for cell in row:
                for m in EMAIL.findall(cell):
                    out.add(m.lower())
    else:
        for m in EMAIL.findall(text):
            out.add(m.lower())
    return sorted(out)


def classify(e: str) -> str | None:
    if OWNER.search(e):
        return "owner/self"
    if JUNK.search(e):
        return "system/junk sender"
    return None


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    path = Path(args[0])
    if not path.exists():
        sys.exit(f"no such file: {path}")
    attach = next((a for a in args if a != "--yes" and not a.endswith(".csv")
                   and a != path.name), None)
    emails = extract(path)
    good = [e for e in emails if not classify(e)]
    junk = [(e, classify(e)) for e in emails if classify(e)]
    print(f"found {len(emails)} emails — mailable {len(good)}, filtered {len(junk)}\n")
    for e in good:
        print(f"  {e}")
    for e, why in junk:
        print(f"  (skip {why}) {e}")
    print("\npaste-ready:")
    print("  " + " ".join(f"--email={e}" for e in good))
    if attach and "--yes" in args:
        root = Path(__file__).resolve().parents[3]
        survey = root / "offer-survey-ops/scripts/offer_survey.py"
        import subprocess
        cmd = [sys.executable, str(survey), "audience", attach,
               *(f"--email={e}" for e in good)]
        print("\n" + subprocess.run(cmd, capture_output=True, text=True).stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
