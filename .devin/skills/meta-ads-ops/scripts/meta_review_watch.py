#!/usr/bin/env python3
"""meta_review_watch.py — watch Meta restriction flags and alert on change.

Runs `meta_ads_audit.py quick`, diffs the flag set against the last known
state (state.json in this dir), and on ANY change sends an alert via
tg_alert.py (severity: intel) and ntfy. Run on a schedule (Windows Task /
cron) — e.g. every 30 min — so Meta's review lift is caught the moment it
lands and the downstream chain can fire.

Usage:
  meta_review_watch.py          # one check (for schedulers)
  meta_review_watch.py --loop 30  # loop every N minutes
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "review_state.json")
AUDIT = os.path.join(HERE, "meta_ads_audit.py")
TG = os.path.normpath(os.path.join(
    HERE, "..", "..", "telegram-alert-ops", "scripts", "tg_alert.py"))


def audit():
    out = subprocess.run([sys.executable, "-X", "utf8", AUDIT, "quick"],
                         capture_output=True, text=True, timeout=120).stdout
    flags = {}
    for line in out.splitlines():
        m = re.match(r"(\S+)\s*\|\s*(.*)", line.strip())
        if m:
            flags[m.group(1)] = m.group(2).strip()
    return flags


def alert(txt):
    for cmd, args in (
        (TG, ["intel", txt]),
        (None, ["ntfy", "pub", "-T", "meta-review", "cloudless-ops", txt]),
    ):
        try:
            if cmd:
                subprocess.run([sys.executable, "-X", "utf8", cmd, *args],
                               timeout=20, capture_output=True)
            else:
                subprocess.run(args, timeout=20, capture_output=True, shell=True)
        except Exception:
            pass


def check():
    now = audit()
    prev = json.load(open(STATE)) if os.path.exists(STATE) else {}
    json.dump(now, open(STATE, "w"), indent=1)
    changed = {k: (prev.get(k), v) for k, v in now.items() if prev.get(k) != v}
    if changed:
        msg = "Meta restriction change: " + "; ".join(
            f"{k} {a}->{b}" for k, (a, b) in changed.items())
        print(msg)
        alert(msg)
        # lifted? chain advice
        if any(v and "restricted" not in v and "disabled" not in v
               for _, v in changed.values()):
            alert("Meta flag may have LIFTED — run wa_onboard_retry + "
                  "business-verification flow")
    else:
        print("no change:", {k: v[:40] for k, v in now.items()})
    return now


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0)
    a = ap.parse_args()
    if a.loop:
        while True:
            try:
                check()
            except Exception as e:
                print("check err:", e)
            time.sleep(a.loop * 60)
    else:
        check()
