#!/usr/bin/env python3
"""Watchdog for Nextcloud-Apache wedge states on omv (aarch64).

Background: Apache children segfault under heavy PHP load (app updates,
bulk preview scans — opcache JIT on ARM64). Docker's `status.php`
healthcheck survives because it hits a still-live child between crashes,
so `docker ps` reports "healthy" while every real PHP route 502s.

Detection (either condition = wedged):
  1. App-route probe inside the container fails N consecutive times —
     `curl -sf localhost/apps/intravox/api/health` exercises real PHP
     (autoload + config), unlike the trivial /status.php endpoint.
  2. Segfault storm: >= SEGFAULT_THRESHOLD "exit signal Segmentation
     fault" lines in `docker logs --since WINDOW` .

Action: `docker restart nextcloud`, syslog, ntfy notice. Cooldown so a
persistently broken container isn't restarted in a loop.

State file: /var/lib/nextcloud-wedge-watchdog/state.json
Units:      nextcloud-wedge-watchdog.service + .timer (every 2 min)
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CONTAINER = "nextcloud"
PROBE_URL = "http://localhost/apps/intravox/api/health"
PROBE_TIMEOUT = 10
FAIL_STREAK = 2                 # consecutive probe failures before acting
SEGFAULT_WINDOW_MIN = 4
SEGFAULT_THRESHOLD = 5
RESTART_COOLDOWN_SEC = 1800     # at most one restart per 30 min
STATE = Path("/var/lib/nextcloud-wedge-watchdog/state.json")
# ntfy.cloudless.gr sits behind Cloudflare Access (302 SSO challenge the
# watchdog can never answer) — publish over the local k3s NodePort instead.
# Bearer token comes from EnvironmentFile /etc/nextcloud-wedge-watchdog.env
# (root:root 0600) — never embed it here, this file lands in git.
NTFY_URL = os.environ.get("NTFY_URL", "http://127.0.0.1:30080/nextcloud-ops")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")


def log(msg: str) -> None:
    subprocess.run(["logger", "-t", "nc-wedge-watchdog", msg], check=False)
    print(msg, flush=True)


def notify(title: str, body: str, priority: str = "4") -> None:
    try:
        headers = {"Title": title, "Priority": priority, "Tags": "warning"}
        if NTFY_TOKEN:
            headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
        req = urllib.request.Request(
            NTFY_URL,
            data=body.encode(),
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception as exc:  # alerting must never break the watchdog
        log(f"ntfy notify failed (ignored): {exc}")


def sh(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"fail_streak": 0, "last_restart": 0}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def probe_app_route() -> tuple[bool, str]:
    rc, out = sh([
        "docker", "exec", CONTAINER, "curl", "-sf", "--max-time",
        str(PROBE_TIMEOUT), "-o", "/dev/null", "-w", "%{http_code}", PROBE_URL,
    ])
    code = out.strip().splitlines()[-1] if out.strip() else "000"
    return rc == 0 and code == "200", code


def segfault_count() -> int:
    rc, out = sh([
        "docker", "logs", CONTAINER, "--since", f"{SEGFAULT_WINDOW_MIN}m",
    ], timeout=30)
    return out.count("exit signal Segmentation fault") if rc == 0 else 0


def main() -> int:
    state = load_state()
    ok, code = probe_app_route()
    segs = segfault_count()

    wedged = False
    reason = ""
    if not ok:
        state["fail_streak"] = state.get("fail_streak", 0) + 1
        if state["fail_streak"] >= FAIL_STREAK:
            wedged = True
            reason = f"app-route probe failing (HTTP {code}) x{state['fail_streak']}"
    else:
        if state.get("fail_streak", 0) >= FAIL_STREAK:
            notify("Nextcloud recovered", f"App route back to 200 after {state['fail_streak']} failed probes", "3")
        state["fail_streak"] = 0
    if segs >= SEGFAULT_THRESHOLD:
        wedged = True
        reason = (reason + "; " if reason else "") + f"{segs} segfaults/{SEGFAULT_WINDOW_MIN}min"

    if not wedged:
        save_state(state)
        return 0

    now = time.time()
    if now - state.get("last_restart", 0) < RESTART_COOLDOWN_SEC:
        log(f"wedged ({reason}) but inside restart cooldown — skipping")
        save_state(state)
        return 1

    log(f"WEDGED: {reason} — restarting {CONTAINER}")
    rc, out = sh(["docker", "restart", CONTAINER], timeout=120)
    state["last_restart"] = now
    state["fail_streak"] = 0
    save_state(state)
    if rc == 0:
        notify("Nextcloud auto-restarted", f"Apache wedge detected: {reason}. Container restarted by watchdog.")
        log("restart complete")
        return 0
    notify("Nextcloud wedge restart FAILED", f"{reason}. docker restart rc={rc}: {out[-300:]}", "5")
    log(f"restart FAILED rc={rc}: {out[-200:]}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
