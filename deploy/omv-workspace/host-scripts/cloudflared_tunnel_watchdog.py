#!/usr/bin/env python3
"""Watchdog for the omv-cloudless Cloudflare Tunnel (cloudflared-omv).

Background: omv's WAN (CGNAT uplink) drops all 4 QUIC connections for
~1-8 min at a time, several times a week. cloudflared retries on its own,
but a wedged or half-registered daemon leaves cloud/office/signal/
push.cloudless.gr serving 502/503 until something notices.

Detection (either condition = act):
  1. cloudflared metrics /ready endpoint fails N consecutive times —
     /ready only reports 200 while the tunnel has registered edge
     connections, so a fail means zero usable conns.
  2. The cloudflared-omv container is not running at all.

Action: `docker restart cloudflared-omv` (or `docker compose up -d` when
the container is gone), syslog, ntfy notice. Cooldown so a persistently
down WAN isn't restarted in a loop — a restart during a WAN outage is
harmless but pointless, and each restart blips the tunnel for ~10s.

State file: /var/lib/cloudflared-tunnel-watchdog/state.json
Units:      cloudflared-tunnel-watchdog.service + .timer (every 2 min)
"""

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

CONTAINER = "cloudflared-omv"
COMPOSE_FILE = os.path.expanduser("~/cloudflared/docker-compose.yml")
READY_URL = "http://127.0.0.1:20241/ready"
PROBE_TIMEOUT = 8
FAIL_STREAK = 3                 # ~6 min of zero registered conns before acting
RESTART_COOLDOWN_SEC = 1800     # at most one restart per 30 min
STATE = Path("/var/lib/cloudflared-tunnel-watchdog/state.json")
# Same alerting lane as nextcloud-wedge-watchdog (local k3s NodePort,
# no Cloudflare Access dependency). Token from env file if configured.
NTFY_URL = os.environ.get("NTFY_URL", "http://127.0.0.1:30080/nextcloud-ops")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")


def log(msg: str) -> None:
    subprocess.run(["logger", "-t", "cf-tunnel-watchdog", msg], check=False)
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
        return {"fail_streak": 0, "last_restart": 0}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))


def ready() -> bool:
    try:
        with urllib.request.urlopen(READY_URL, timeout=PROBE_TIMEOUT) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    st = load_state()

    rc, out = sh(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER])
    container_down = rc != 0 or "true" not in out

    if ready():
        if st.get("fail_streak", 0):
            log(f"tunnel ready again after {st['fail_streak']} failed probe(s)")
        st["fail_streak"] = 0
        save_state(st)
        return 0

    if container_down:
        log(f"container {CONTAINER} not running — bringing up via compose")
        rc, out = sh(
            ["docker", "compose", "-f", COMPOSE_FILE, "up", "-d"], timeout=120
        )
        if rc == 0:
            notify("cloudflared-omv was down", f"Recovered via compose up -d.\n{out[-400:]}")
        else:
            notify("cloudflared-omv down, compose failed", out[-400:], priority="5")
        return 0

    st["fail_streak"] = st.get("fail_streak", 0) + 1
    log(f"/ready probe failed (streak={st['fail_streak']})")

    now = time.time()
    if (
        st["fail_streak"] >= FAIL_STREAK
        and now - st.get("last_restart", 0) >= RESTART_COOLDOWN_SEC
    ):
        log(f"streak {st['fail_streak']} >= {FAIL_STREAK} — restarting {CONTAINER}")
        rc, out = sh(["docker", "restart", CONTAINER], timeout=60)
        st["last_restart"] = now
        st["fail_streak"] = 0
        if rc == 0:
            notify(
                "cloudflared-omv restarted",
                "Zero registered tunnel connections for ~6 min; container restarted.",
            )
        else:
            notify("cloudflared-omv restart FAILED", out[-400:], priority="5")

    save_state(st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
