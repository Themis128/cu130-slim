#!/usr/bin/env python3
"""gate_capture — watch Downloads for credential drops and auto-provision.

Drop conventions (any filename match; first line = the secret):
    bsky*.txt       → bsky password/app-password → bsky_provision connect
    viber*.txt      → viber bot token           → viber_provision --token
    (files are consumed + shredded after a successful wire)

    gate_capture.py scan    # one-shot sweep (cron: */15 * * * *)
    gate_capture.py status  # what's wired, what's still missing
"""
import glob
import json
import os
import subprocess
import sys

DL = "/mnt/c/Users/tbaltzakis/Downloads"
SKILLS = "/home/tbaltzakis/cu130-slim/.devin/skills"
STATE = os.path.join(os.path.dirname(__file__), "..", "state")
DONE = os.path.join(STATE, "gate_done.json")


def _done() -> set:
    os.makedirs(STATE, exist_ok=True)
    try:
        return set(json.load(open(DONE)))
    except Exception:
        return set()


def _mark(k: str) -> None:
    s = _done() | {k}
    json.dump(sorted(s), open(DONE, "w"), indent=0)


def _read_secret(path: str) -> str:
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def _run(script: str, args: list[str]) -> tuple[int, str]:
    r = subprocess.run(
        ["python3", os.path.join(SKILLS, script)] + args,
        capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout + r.stderr)[-400:]


def scan() -> int:
    hits = 0
    for f in sorted(glob.glob(os.path.join(DL, "*"))):
        name = os.path.basename(f).lower()
        if not os.path.isfile(f) or f in _done():
            continue
        if name.startswith("bsky") and name.endswith((".txt", ".key")):
            sec = _read_secret(f)
            if not sec:
                continue
            rc, out = _run("bluesky-ops/scripts/bsky_provision.py",
                           ["connect", "--handle", "cloudless.gr",
                            "--password", sec])
            print(f"bsky drop → rc={rc} {out[:80]}")
            if rc == 0:
                _mark(f); os.remove(f); hits += 1
        elif name.startswith("viber") and name.endswith((".txt", ".key")):
            sec = _read_secret(f)
            if not sec:
                continue
            rc, out = _run("viber-ops/scripts/viber_provision.py",
                           ["--token", sec])
            print(f"viber drop → rc={rc} {out[:80]}")
            if rc == 0:
                _mark(f); os.remove(f); hits += 1
    # auto-harvest: if no drops and channels still missing, try the
    # logged-in browser sessions (bsky.app session, partners.viber.com)
    if hits == 0:
        for plat in ("bsky", "viber"):
            if plat in _done():
                continue
            rc = subprocess.run(
                ["python3", os.path.join(os.path.dirname(__file__),
                                        f"{plat}_harvest.py"), "run"],
                capture_output=True, text=True, timeout=90)
            if rc.returncode == 0:
                print(f"{plat}: auto-harvested + wired"); _mark(plat); hits += 1
    print(f"scan: {hits} wired")
    return hits


def status() -> None:
    for plat, probe in (("bluesky", ["connect", "--help"]),
                        ("viber", ["--verify"])):
        rc, out = _run("viber-ops/scripts/viber_provision.py"
                       if plat == "viber"
                       else "bluesky-ops/scripts/bsky_provision.py",
                       probe if plat == "viber" else ["--help"])
        print(f"{plat}: {'wired' if plat in _done() else 'pending — drop ' + plat + '*.txt in Downloads'}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    {"scan": scan, "status": status}[cmd]()
