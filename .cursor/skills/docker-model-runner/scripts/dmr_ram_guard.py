#!/usr/bin/env python3
"""dmr_ram_guard.py — evict resident DMR models when Windows host RAM starves.

DMR models are CPU-pinned (--n-gpu-layers 0), so weights live in SYSTEM RAM
(~3-10GB resident across the fleet). When Windows free RAM drops, Chrome
renderers OOM -> the :9229 debug Chrome crash-loops -> FB/IG/LI gate sessions
die (2026-11-26 incident class). DMR's own eviction only fires on
load-pressure, never host-pressure — this guard fills that gap.

Design:
  * Reads ~/.local/state/host-gates-ops/host-ram-available-mb — the file
    host_gates_check.py refreshes every ~10min. NO powershell spawn (the
    interop call allocates a console window — popup-free rule). If the file
    is stale (>15min) the pass is skipped rather than measured live.
  * Hysteresis: two consecutive low readings before acting, so transient
    dips don't churn model loads.
  * Cooldown: 20min between unloads.
  * Only unloads when /api/ps actually lists resident models — an empty
    resident set means nothing to free.
  * Unload uses POST /engines/unload {"all": true} — the runner's native
    endpoint (verified live; same call dmr.py uses for OOM heal). NOT the
    Ollama keep_alive=0 trick, which queues a reload before evicting.

Cost when it fires: the next AI call pays a cold model load (~30-60s) or
falls back to Cloudflare Workers AI. Benefit: prevents the host-starvation
-> renderer-OOM -> session-death cascade.

Cron (user crontab, every 5min):
  */5 * * * * /usr/bin/python3 ~/cu130-slim/.devin/skills/docker-model-runner/scripts/dmr_ram_guard.py >> ~/logs/dmr-ram-guard.log 2>&1
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_get, dmr_post  # noqa: E402

RAM_FILE = Path.home() / ".local/state/host-gates-ops/host-ram-available-mb"
STATE_FILE = Path.home() / ".local/state/dmr-ram-guard.json"
LOG_FILE = Path.home() / "logs/dmr-ram-guard.log"
TG_ALERT = Path.home() / "cu130-slim/.devin/skills/telegram-alert-ops/scripts/tg_alert.py"

LOW_MB = 3500          # act below this Windows free-RAM level
RAM_FILE_MAX_AGE = 900  # skip pass if shared file older than 15min
REQUIRED_LOW_STREAK = 2
COOLDOWN_S = 1200      # 20min between unloads
UNLOAD_TIMEOUT = 45


def _log(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as f:
            f.write(f"{time.strftime('%F %T')} {msg}\n")
    except OSError:
        pass


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(st))
    except OSError:
        pass


def host_ram_mb() -> int | None:
    """Windows host free RAM in MB from the shared host_gates_check file.
    Returns None when missing/stale — caller skips (no live spawn)."""
    try:
        if time.time() - RAM_FILE.stat().st_mtime > RAM_FILE_MAX_AGE:
            return None
        return int(RAM_FILE.read_text().strip())
    except Exception:
        return None


def resident_count() -> int:
    raw = dmr_get("/api/ps", timeout=10)
    if not raw:
        return -1  # unreachable — don't act blind
    try:
        return len(json.loads(raw).get("models", []))
    except Exception:
        return -1


def tg_alert(msg: str) -> None:
    try:
        subprocess.run(
            [sys.executable, str(TG_ALERT), "ops", msg],
            capture_output=True, timeout=20,
        )
    except Exception:
        pass


def main() -> int:
    ram = host_ram_mb()
    if ram is None:
        _log("skip: host RAM file missing/stale")
        return 0

    st = _state()
    now = time.time()

    if ram >= LOW_MB:
        if st.get("low_streak"):
            _log(f"recovered: host RAM {ram}MB >= {LOW_MB}MB")
        st["low_streak"] = 0
        _save_state(st)
        return 0

    streak = int(st.get("low_streak", 0)) + 1
    st["low_streak"] = streak
    st["last_ram_mb"] = ram
    _save_state(st)
    _log(f"low RAM {ram}MB (streak {streak}/{REQUIRED_LOW_STREAK})")

    if streak < REQUIRED_LOW_STREAK:
        return 0
    if now - float(st.get("last_unload", 0)) < COOLDOWN_S:
        _log("low RAM persists but unload cooldown active")
        return 0

    n = resident_count()
    if n <= 0:
        _log(f"low RAM {ram}MB but no resident DMR models (n={n}) — nothing to free")
        st["last_unload"] = now  # don't re-check every tick while empty
        _save_state(st)
        return 0

    _log(f"host RAM {ram}MB — unloading {n} resident DMR model(s)")
    res = dmr_post("/engines/unload", {"all": True}, timeout=UNLOAD_TIMEOUT)
    _log(f"unload response: {json.dumps(res)[:300]}")

    # verify via /api/ps
    time.sleep(3)
    left = resident_count()
    st["last_unload"] = now
    _save_state(st)
    if left == 0:
        _log(f"OK: DMR fleet evicted (host RAM was {ram}MB)")
        tg_alert(f"dmr-ram-guard: evicted {n} resident model(s) — host RAM was {ram}MB free")
    else:
        _log(f"WARN: unload unverified — /api/ps still lists {left} model(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
