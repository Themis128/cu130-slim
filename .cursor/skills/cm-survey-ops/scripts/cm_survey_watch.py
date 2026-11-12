#!/usr/bin/env python3
"""cm_survey_watch.py — consume a dropped screen-recording mp4 and run the
CM Standard-tier Qualtrics submission unattended.

Drop any *.mp4 into ~/Downloads (or /tmp) — newest wins. The watcher runs
cm_survey_drive.py once, then renames the file to *.consumed so it never
fires twice. The registered address is baked into the drive (env
override); rc=2 means no CDP endpoint — file kept, reminder ≤1/6h.

    cm_survey_watch.py          # one sweep (cron lane)
    cm_survey_watch.py --dry-run
"""
import glob
import json
import os
import subprocess
import sys
import time

DROP_DIRS = ["/mnt/c/Users/tbaltzakis/Downloads", "/tmp"]
SKIP = (".consumed", ".part", ".crdownload", ".tmp")
STATE = os.path.expanduser("~/.local/state/cm-survey-watch")
DONE = os.path.join(STATE, "done.json")
BLOCKED = os.path.join(STATE, "blocked.json")
BLOCKED_REMIND = 6 * 3600
DRIVE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "cm_survey_drive.py")
LOG = os.path.expanduser("~/logs/cm-survey-watch.log")
TG_ALERT = ("/home/tbaltzakis/cu130-slim/.devin/skills/"
            "telegram-alert-ops/scripts/tg_alert.py")


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def tg(text):
    try:
        subprocess.run(["python3", TG_ALERT, "ops", text], timeout=30)
    except Exception as e:
        log(f"tg failed: {e}")


def done_set():
    try:
        return set(json.load(open(DONE)))
    except Exception:
        return set()


def mark(p):
    os.makedirs(STATE, exist_ok=True)
    s = done_set() | {os.path.abspath(p)}
    json.dump(sorted(s), open(DONE, "w"), indent=0)


def _blocked():
    try:
        return json.load(open(BLOCKED))
    except Exception:
        return {}


def _block_alert_due():
    return time.time() - _blocked().get("last", 0) > BLOCKED_REMIND


def _mark_blocked():
    os.makedirs(STATE, exist_ok=True)
    json.dump({"last": time.time()}, open(BLOCKED, "w"))


def candidates():
    out = []
    for d in DROP_DIRS:
        for p in glob.glob(os.path.join(d, "*.mp4")):
            if any(p.endswith(s) for s in SKIP):
                continue
            out.append((os.path.getmtime(p), p))
    return [p for _, p in sorted(out, reverse=True)]


def main():
    dry = "--dry-run" in sys.argv
    done = done_set()
    for mp4 in candidates():
        if os.path.abspath(mp4) in done:
            continue
        # skip files still being written
        try:
            if time.time() - os.path.getmtime(mp4) < 20:
                continue
        except OSError:
            continue
        log(f"candidate: {mp4}")
        cmd = ["python3", DRIVE, mp4]
        if dry:
            cmd.append("--dry-run")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        tail = (r.stdout + r.stderr).strip().splitlines()[-5:]
        log(f"drive rc={r.returncode}: " + " | ".join(tail))
        if r.returncode == 0:
            mark(mp4)
            os.rename(mp4, mp4 + ".consumed")
            tg(f"📋 CM Standard-tier survey submitted — recording {os.path.basename(mp4)} "
               "consumed. LinkedIn review takes days; check the portal for status.")
            return 0
        # rc=2 = missing CM_STREET/ZIP or no CDP — keep file, remind ≤1/6h
        if r.returncode == 2:
            if _block_alert_due():
                tg("⚠️ cm-survey-watch: mp4 found but drive couldn't run — "
                   "no CDP endpoint reachable (debug Chrome down?). File kept; "
                   "will retry once unblocked.")
                _mark_blocked()
            return 2
        # other failure: keep file for manual inspection, mark to avoid loop
        mark(mp4)
        tg(f"❌ cm-survey-watch: drive failed on {os.path.basename(mp4)} "
           f"(rc={r.returncode}) — file kept for manual retry. "
           + " | ".join(tail[-2:]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
