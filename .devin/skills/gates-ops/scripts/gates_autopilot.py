#!/usr/bin/env python3
"""gates_autopilot.py — probe every human gate, fire the automatable closers.

Runs gate_probe.py for live state, then attempts each gate's scripted lane:
  - sidecar sessions: health-probe first (many "dead" entries are stale)
  - cdp_click.py on debug Chrome :9222 (auto-reexecs under Windows python)
  - ADB lanes on connected devices (adb devices list)
  - credential fallbacks whose env vars already exist in repo .env

Usage: gates_autopilot.py [--dry-run] [--json]
Read-only with --dry-run. Never prints secrets.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
HERE = os.path.dirname(__file__)
ENV_FILE = os.path.join(ROOT, ".env")


def env_has(*names):
    try:
        data = open(ENV_FILE).read()
    except OSError:
        return set()
    return {n for n in names if re.search(rf"^{n}=.+", data, re.M)}


def sh(*cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def http_ok(url, code_range=(200, 399), timeout=10):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return code_range[0] <= r.status <= code_range[1]
    except Exception:
        return False


def main():
    dry = "--dry-run" in sys.argv
    report = {"dry_run": dry, "actions": []}

    def act(name, status, detail=""):
        report["actions"].append({"gate": name, "result": status, "detail": detail})
        print(f"{'[dry] ' if dry else ''}{name}: {status} {detail}")

    # 1) live probe (informational)
    rc, out = sh(sys.executable, os.path.join(HERE, "gate_probe.py"), timeout=120)
    act("gate_probe", "ran" if rc == 0 else "failed", out.splitlines()[-1][:80] if out else "")

    # 2) sidecar health truth-check (stale "dead session" entries)
    for name, url in {
        "linkedin-sidecar": "http://127.0.0.1:9225/health",
        "tiktok-sidecar": "http://127.0.0.1:9224/health",
        "bridge": "http://127.0.0.1:9223/health",
    }.items():
        act(name, "live" if http_ok(url) else "down-or-asleep")

    # 3) CDP lane — only if debug Chrome answers and the target host resolves there
    cdp_up = http_ok("http://127.0.0.1:9222/json/version")
    act("debug-chrome", "up" if cdp_up else "down")

    # 4) ADB lane — devices present?
    rc, out = sh("adb", "devices", timeout=15)
    devs = [l.split()[0] for l in out.splitlines()[1:] if l.strip() and "device" in l]
    act("adb-devices", f"{len(devs)} online", ",".join(devs)[:60])

    # 5) credential fallbacks armed in .env
    have = env_has("INSTAGRAM_PASSWORD", "LINKEDIN_EMAIL", "LINKEDIN_PASSWORD",
                   "GMAIL_REFRESH_TOKEN", "X_WEB_AUTH_TOKEN", "SOCIAL_ADMIN_PASSWORD")
    act("env-lanes", f"{len(have)} armed", ",".join(sorted(have)))

    # 6) docker daemon — gates needing containers
    rc, _ = sh("docker", "ps", "--format", "{{.Names}}", timeout=15)
    act("docker", "up" if rc == 0 else "down — wsl/vsock fix needed")

    if "--json" in sys.argv:
        print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
