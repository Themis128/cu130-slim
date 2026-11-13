#!/usr/bin/env python3
"""Watch Microsoft 365 / new-Outlook recovery; TG-alert #ops when services heal.

Degraded signatures observed during the 2026-11-12/13 global outage:
  - https://outlook.office.com/mail/      -> HTTP 417 (Expectations Failed) or SSL timeouts
  - https://arc.msn.com/v4/               -> HTTP 500 (substrate backend for new Outlook)
  - https://outlook.office365.com/owa/    -> 417 or SSL handshake timeouts (was 302 pre-outage)

Usage:
  ms365_outlook_watch.py          # one probe; prints state, alerts on recovery transition
  ms365_outlook_watch.py --wait   # block: poll every 5 min (max ~8h), exit 0 on ALL_CLEAR

Cron (user): */15 * * * * python3 <repo>/.devin/skills/outlook-win11-ops/scripts/ms365_outlook_watch.py >> ~/logs/ms365-outlook-watch.log 2>&1
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

STATE = os.path.expanduser("~/.local/state/ms365-outlook-watch.json")
TG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "telegram-alert-ops", "scripts", "tg_alert.py",
)
TG = os.path.normpath(TG)

CHECKS = {
    "outlook_mail": "https://outlook.office.com/mail/",
    "arc_substrate": "https://arc.msn.com/v4/",
    "owa": "https://outlook.office365.com/owa/",
}
BAD_CODES = {0, 417, 500, 502, 503, 504}


def probe(url, timeout=15):
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def once():
    results = {k: probe(u) for k, u in CHECKS.items()}
    degraded = any(c in BAD_CODES or c >= 500 for c in results.values())
    prev = {}
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE))
        except Exception:
            pass
    was_down = prev.get("down", True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"down": degraded, "results": results, "ts": now}, open(STATE, "w"))
    if not degraded and was_down:
        msg = (
            "P2 Microsoft 365/Outlook recovered — probes: "
            + ", ".join(f"{k}={v}" for k, v in results.items())
            + " — new-Outlook OOBE + account adds can proceed"
        )
        subprocess.run(["python3", TG, msg], capture_output=True)
        print("RECOVERED -> alerted", results)
        return True
    print(("still degraded " if degraded else "healthy ") + json.dumps(results))
    return not degraded


def main():
    if "--wait" in sys.argv:
        for _ in range(96):  # ~8h at 5-min intervals
            if once():
                print("ALL_CLEAR")
                return 0
            time.sleep(300)
        print("TIMEOUT_STILL_DEGRADED")
        return 2
    return 0 if once() else 1


if __name__ == "__main__":
    sys.exit(main())
