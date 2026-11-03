#!/usr/bin/env python3
"""act_assist.py — one watcher for the 4 remaining human acts.

    python3 act_assist.py              # status of all 4 acts
    python3 act_assist.py --watch      # poll, auto-finish each as it lands
    python3 act_assist.py --act NAME   # status/prep for one act

Acts: x-login | insta-pass | meta-coadmin | phone-unlock
Each: detects completion -> runs the finisher -> pings ntfy cloudless-ops.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

CDP = os.environ.get("CDP_URL", "http://100.83.120.93:9223")
NTFY = os.environ.get("NTFY", "http://192.168.1.200:30080")
SER = os.environ.get("ADB_SERIAL", "192.168.1.77:5555")
ADB = os.environ.get("ADB", "adb")


def ntfy(msg: str) -> None:
    try:
        req = urllib.request.Request(NTFY + "/cloudless-ops",
                                     data=msg.encode(),
                                     headers={"Title": "human-act-assist"})
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass


def cdp_targets() -> list:
    try:
        return json.load(urllib.request.urlopen(CDP + "/json", timeout=6))
    except Exception:
        return []


def x_logged_in() -> bool:
    for t in cdp_targets():
        u = t.get("url", "")
        if "x.com/home" in u or "twitter.com/home" in u:
            return True
    return False


def insta_pass_set() -> bool:
    for p in (".env", "social-automation/.env", "social-automation/backend/.env"):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8", errors="ignore"):
                if line.startswith("INSTAGRAM_PASSWORD=") and len(line.split("=", 1)[1].strip()) > 3:
                    return True
    return False


def phone_awake() -> bool:
    try:
        out = subprocess.run([ADB, "-s", SER, "shell",
                              "dumpsys power | grep mWakefulness"],
                             capture_output=True, text=True, timeout=10).stdout
        return "Awake" in out
    except Exception:
        return False


def finish_x() -> None:
    ntfy("X session detected — OAuth approve + TOTP path armed")
def finish_insta() -> None:
    ntfy("INSTAGRAM_PASSWORD set — run session_bootstrap.py")
def finish_unlock() -> None:
    ntfy("Phone awake — meta_totp.py --capture ready")
def finish_coadmin() -> None:
    ntfy("Meta co-admin/portfolio step done — re-check WABA surfaces")


ACTS = {
    "x-login": {"check": x_logged_in, "finish": finish_x,
                "hint": "Google sign-in in the debug window (x.com tab)"},
    "insta-pass": {"check": insta_pass_set, "finish": finish_insta,
                   "hint": "INSTAGRAM_PASSWORD=... in .env"},
    "meta-coadmin": {"check": lambda: False, "finish": finish_coadmin,
                     "hint": "portfolio co-admin or new portfolio — no detector"},
    "phone-unlock": {"check": phone_awake, "finish": finish_unlock,
                     "hint": "unlock the S22+ once"},
}


def status() -> None:
    for name, a in ACTS.items():
        print(f"{'OK ' if a['check']() else '..'} {name}: {a['hint']}")


def watch() -> None:
    done = set()
    while len(done) < 2:  # x-login and insta-pass are the detectable pair
        for name, a in ACTS.items():
            if name in done or name == "meta-coadmin":
                continue
            try:
                if a["check"]():
                    a["finish"]()
                    done.add(name)
                    print("[done]", name)
            except Exception:
                pass
        time.sleep(20)


def main() -> int:
    if "--watch" in sys.argv:
        watch()
        return 0
    if "--act" in sys.argv:
        n = sys.argv[sys.argv.index("--act") + 1]
        a = ACTS.get(n)
        print(f"{n}: {'done' if a['check']() else 'pending — ' + a['hint']}")
        return 0
    status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
