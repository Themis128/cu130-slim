#!/usr/bin/env python3
"""x_supervisor.py — keeps x_daemon.py alive indefinitely.

x_daemon.py has an internal 12h cap; this wrapper restarts it on exit so the
X recovery lane survives past the cap and any crash. Runs hidden on Windows:

    python C:\\Users\\tbaltzakis\\x_supervisor.py
"""
import subprocess, sys, time, os

DAEMON = r"C:\Users\tbaltzakis\x_daemon.py"
LOG = r"C:\Users\tbaltzakis\x_supervisor_log.txt"
RESTART_DELAY = 60

def log(*a):
    line = time.strftime("%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        open(LOG, "a", encoding="utf-8").write(line + "\n")
    except Exception:
        pass

def main():
    n = 0
    while True:
        n += 1
        log(f"=== launching x_daemon (run {n}) ===")
        try:
            rc = subprocess.call([sys.executable, DAEMON])
            log(f"x_daemon exited rc={rc} — restart in {RESTART_DELAY}s")
        except Exception as e:
            log("spawn error:", e)
        time.sleep(RESTART_DELAY)

if __name__ == "__main__":
    main()
