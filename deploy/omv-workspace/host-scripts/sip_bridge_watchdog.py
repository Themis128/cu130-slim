#!/usr/bin/env python3
"""Watchdog + self-healer for the talk-sip-bridge container (Talk SIP dial-in).

The bridge answers inbound SIP calls on the linphone account
(sip:cloudlessgr@sip.linphone.org), plays the IVR, and hands callers into
Nextcloud Talk rooms. Two failure modes matter:

  1. Container down or wedged — nobody answers inbound calls.
  2. SIP registration silently lost — container runs, control API answers,
     but `registered` is false and inbound calls never reach the bridge.
     (linphone registrations expire; the daemon re-registers itself, but a
     stale restored state or a missed re-REGISTER leaves the line dead.)

Heal ladder (cheapest first):
  a. unregistered but API alive -> POST /toggle off + on forces a real
     REGISTER without a restart (fast, harmless).
  b. still unregistered after FAIL_STREAK consecutive probes (~6 min) ->
     docker restart.
  c. container not running at all -> docker compose up -d.

Guards:
  - Never restart while `active_call` is set — a live phone participant
    would be dropped mid-call. The streak is kept; restart happens on the
    next pass after the call ends.
  - RESTART_COOLDOWN_SEC caps restart storms.
  - Alerting must never break the watchdog (ntfy failures are logged only).

State file: /var/lib/sip-bridge-watchdog/state.json
Units:      sip-bridge-watchdog.service + .timer (every 2 min)
Repo:       deploy/omv-workspace/host-scripts/sip_bridge_watchdog.py
"""

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

CONTAINER = "talk-sip-bridge"
COMPOSE_FILE = "/srv/dev-disk-by-uuid-fa6231ab-eae7-40ea-a4b6-400f767a89d7/talk-sip-bridge/compose.yml"
STATUS_URL = "http://127.0.0.1:8765/status"
TOGGLE_URL = "http://127.0.0.1:8765/toggle"
PROBE_TIMEOUT = 8
FAIL_STREAK = 3                 # ~6 min unregistered before a restart
RESTART_COOLDOWN_SEC = 1800     # at most one restart per 30 min
STATE = Path("/var/lib/sip-bridge-watchdog/state.json")
# Same alerting lane as nextcloud-wedge-watchdog / cloudflared-tunnel-watchdog
# (local k3s NodePort, token from the shared env file via systemd).
NTFY_URL = os.environ.get("NTFY_URL", "http://127.0.0.1:30080/nextcloud-ops")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")


def log(msg: str) -> None:
    subprocess.run(["logger", "-t", "sip-bridge-watchdog", msg], check=False)
    print(msg, flush=True)


def notify(title: str, body: str, priority: str = "4") -> None:
    try:
        headers = {"Title": title, "Priority": priority, "Tags": "warning"}
        if NTFY_TOKEN:
            headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
        req = urllib.request.Request(NTFY_URL, data=body.encode(), headers=headers)
        urllib.request.urlopen(req, timeout=8)
    except Exception as exc:  # alerting must never break the watchdog
        log(f"ntfy notify failed (ignored): {exc}")


def sh(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"fail_streak": 0, "last_restart": 0, "last_toggle": 0}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))


def bridge_status() -> dict | None:
    """Control-API snapshot, or None when the API is unreachable."""
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=PROBE_TIMEOUT) as r:
            if r.status != 200:
                return None
            return json.load(r)
    except Exception:
        return None


def is_healthy(status: dict | None) -> bool:
    return bool(status and status.get("registered"))


def call_active(status: dict | None) -> bool:
    if not status:
        return False
    if status.get("active_call"):
        return True
    return any(line.get("active_call") for line in status.get("lines", []))


def force_reregister() -> bool:
    """Toggle the line off+on through the control API, then re-probe."""
    try:
        urllib.request.urlopen(
            urllib.request.Request(TOGGLE_URL, data=b"", method="POST"),
            timeout=PROBE_TIMEOUT,
        )
        time.sleep(3)
        urllib.request.urlopen(
            urllib.request.Request(TOGGLE_URL, data=b"", method="POST"),
            timeout=PROBE_TIMEOUT,
        )
        time.sleep(8)
    except Exception as exc:
        log(f"toggle heal failed: {exc}")
    return is_healthy(bridge_status())


def main() -> int:
    st = load_state()
    status = bridge_status()

    # Container gone or stopped -> bring it up; no point probing further.
    rc, out = sh(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER])
    container_down = rc != 0 or "true" not in out
    if container_down:
        log(f"container {CONTAINER} not running — bringing up via compose")
        rc, out = sh(
            ["docker", "compose", "-f", COMPOSE_FILE, "up", "-d"], timeout=180
        )
        if rc == 0:
            notify("talk-sip-bridge was down", f"Recovered via compose up -d.\n{out[-400:]}")
        else:
            notify("talk-sip-bridge down, compose failed", out[-400:], priority="5")
        st["fail_streak"] = 0
        save_state(st)
        return 0

    if is_healthy(status):
        if st.get("fail_streak", 0):
            log(f"registered again after {st['fail_streak']} failed probe(s)")
        st["fail_streak"] = 0
        save_state(st)
        return 0

    # Running but unregistered (or API dead). Try the cheap heal first.
    last_error = (status or {}).get("last_error")
    log(f"unhealthy: registered={status and status.get('registered')} last_error={last_error}")

    now = time.time()
    if now - st.get("last_toggle", 0) >= 300:
        st["last_toggle"] = now
        if force_reregister():
            log("toggle heal succeeded — line registered again")
            notify(
                "talk-sip-bridge re-registered",
                f"Registration was lost; control-API toggle restored it.\nlast_error={last_error}",
            )
            st["fail_streak"] = 0
            save_state(st)
            return 0

    st["fail_streak"] = st.get("fail_streak", 0) + 1
    log(f"fail_streak={st['fail_streak']}")

    if (
        st["fail_streak"] >= FAIL_STREAK
        and now - st.get("last_restart", 0) >= RESTART_COOLDOWN_SEC
    ):
        if call_active(status):
            log("active call in progress — deferring restart")
            save_state(st)
            return 0
        log(f"streak {st['fail_streak']} >= {FAIL_STREAK} — restarting {CONTAINER}")
        rc, out = sh(["docker", "restart", CONTAINER], timeout=60)
        st["last_restart"] = now
        st["fail_streak"] = 0
        if rc == 0:
            notify(
                "talk-sip-bridge restarted",
                f"Unregistered for ~6 min despite toggle heal; container restarted.\nlast_error={last_error}",
            )
        else:
            notify("talk-sip-bridge restart FAILED", out[-400:], priority="5")

    save_state(st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
