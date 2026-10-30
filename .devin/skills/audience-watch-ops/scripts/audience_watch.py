#!/usr/bin/env python3
"""audience_watch.py — watch Downloads for contact exports, auto-attach.

Polls a watch dir for Connections*.csv / *.csv / *.txt email lists,
harvests the addresses (contact-harvest-ops rules), dedupes against
already-queued recipients, and attaches new ones to the survey campaign.

    audience_watch.py                         # sweep once + exit
    audience_watch.py --daemon                # poll every 30s
    audience_watch.py --campaign <CID>        # override default campaign
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

WATCH = [Path("/mnt/c/Users/tbaltzakis/Downloads"), Path.home() / "Downloads"]
CAMPAIGN = "9f98e2ce-24da-46f3-a19f-8d7e0e875270"
EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
JUNK = re.compile(r"noreply|no-reply|notification|mailer|postmaster|@mail\.|"
                  r"linkedin\.|skool\.|messenger\.local$|test\.dev$|example\.", re.I)
OWNER = re.compile(r"baltzakis|themis|tbaltzakis|cloudless\.gr", re.I)
SURVEY = Path(__file__).resolve().parents[3] / "offer-survey-ops/scripts/offer_survey.py"
SEEN = Path("/tmp/audience_watch_seen.txt")
SEEN.touch(exist_ok=True)


def harvest(path: Path) -> list[str]:
    out = set()
    for m in EMAIL.findall(path.read_text(errors="replace")):
        e = m.lower()
        if not JUNK.search(e) and not OWNER.search(e):
            out.add(e)
    return sorted(out)


def attach(campaign: str, emails: list[str]) -> int:
    seen = set(SEEN.read_text().splitlines())
    new = [e for e in emails if e not in seen]
    if not new:
        return 0
    cmd = [sys.executable, str(SURVEY), "audience", campaign,
           *(f"--email={e}" for e in new)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode == 0:
        seen.update(new)
        SEEN.write_text("\n".join(sorted(seen)) + "\n")
    return len(new)


def sweep(campaign: str) -> int:
    total = 0
    for d in WATCH:
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in (".csv", ".txt") and (
                "connection" in f.name.lower() or f.name.lower().startswith("survey")
            ):
                total += attach(campaign, harvest(f))
    return total


def main() -> int:
    args = sys.argv[1:]
    cid = args[args.index("--campaign") + 1] if "--campaign" in args else CAMPAIGN
    if "--daemon" in args:
        while True:
            n = sweep(cid)
            if n:
                print(f"attached {n} new recipients")
            time.sleep(30)
    else:
        n = sweep(cid)
        print(f"attached {n} new recipients" if n else "no new contact exports found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
