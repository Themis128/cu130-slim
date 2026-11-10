#!/usr/bin/env python3
"""ig_fb_session_heal.py — keep the facebook-browser-sidecar FB session alive.

The instagram_reauth lane (and every FB/IG publish path) dies on a
login_wall / 2FA checkpoint when the sidecar's Facebook session expires.
This host-side healer closes that gap unattended:

  1. GET /session/validate on the sidecar (via ``docker exec`` + node
     fetch — the sidecar publishes no host port).
  2. If logged out -> POST /login with FACEBOOK_USERNAME/FACEBOOK_PASSWORD.
  3. ``two_factor_required`` -> pull the SMS code off the phone via
     phone-2fa-ops ``fb_code.py`` (ADB notification dump) and retry
     /login with ``verification_code``.
  4. Drive post-checkpoint clicks ("This was me", "Save", "Continue")
     via /debug/eval, then re-validate.

Login attempts are throttled (default 30 min between attempts) — repeated
FB logins themselves trigger checkpoints. Validation probes are cheap and
always run.

Env: reads FACEBOOK_USERNAME/FACEBOOK_PASSWORD from ~/cu130-slim/.env.
Log:   ~/logs/ig-fb-session-heal.log
State: ~/.local/state/ig-fb-heal/state.json
Usage: ig_fb_session_heal.py [--once] [--min-interval 1800]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
REPO = HOME / "cu130-slim"
ENV_FILE = REPO / ".env"
SIDECAR_CONTAINER = os.environ.get("FB_SIDECAR_CONTAINER", "facebook-browser-sidecar")
SIDECAR_URL = "http://localhost:9226"
FB_CODE = REPO / ".devin/skills/phone-2fa-ops/scripts/fb_code.py"
TG_ALERT = REPO / ".devin/skills/telegram-alert-ops/scripts/tg_alert.py"
STATE_DIR = HOME / ".local/state/ig-fb-heal"
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = HOME / "logs/ig-fb-session-heal.log"
DEFAULT_MIN_INTERVAL = 1800  # 30 min between login attempts

# Post-login checkpoint buttons (fbchat flow: save_device -> "This was me"
# -> save device again). Deny/cancel is never clicked.
_CHECKPOINT_JS = """
(() => {
  // Only touch checkpoint/login pages — never click arbitrary FB UI.
  if (!/checkpoint|two_factor|approvals|\\/login/.test(location.href)) {
    return 'not-checkpoint:' + location.href.slice(0, 60);
  }
  const els = [...document.querySelectorAll(
      'button,[role=button],input[type=submit],div[tabindex]')];
  const vis = e => { const r = e.getBoundingClientRect();
                     return r.width > 0 && r.height > 0; };
  const txt = e => (e.innerText || e.value || e.getAttribute('aria-label') || "").trim();
  const deny = /cancel|decline|not now|maybe later|deny|this wasn't me|next items/i;
  const ok = /this was me|^save$|^continue$|^allow$|^confirm$|got it|stay logged in/i;
  const b = els.filter(vis).find(e => ok.test(txt(e)) && txt(e).length < 60 && !deny.test(txt(e)));
  if (b) { b.click(); return 'clicked:' + txt(b).slice(0, 40); }
  return 'no-button';
})()
"""


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def sidecar(path: str, payload: dict | None = None, timeout: int = 120) -> dict:
    """Call the sidecar HTTP API through `docker exec` + node fetch.

    The sidecar only listens inside the compose network (no host port),
    and node:22-slim has no curl — node fetch is the reliable transport.
    """
    req = json.dumps({"path": path, "method": "POST" if payload is not None else "GET",
                      "body": payload})
    js = (
        "let d='';process.stdin.on('data',c=>d+=c).on('end',async()=>{"
        "const r=JSON.parse(d);"
        "const res=await fetch('" + SIDECAR_URL + "'+r.path,{"
        "method:r.method,headers:{'Content-Type':'application/json'},"
        "body:r.body?JSON.stringify(r.body):undefined});"
        "process.stdout.write(await res.text());})"
    )
    try:
        out = subprocess.run(
            ["docker", "exec", "-i", SIDECAR_CONTAINER, "node", "-e", js],
            input=req, capture_output=True, text=True, timeout=timeout,
        )
        if out.returncode != 0:
            return {"error": f"exec rc={out.returncode}: {out.stderr.strip()[:200]}"}
        return json.loads(out.stdout or "{}")
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


def _adb_bin() -> str:
    """Resolve adb: PATH first, then the Windows SDK adb.exe via WSL interop."""
    import shutil
    if shutil.which("adb"):
        return "adb"
    win = Path(
        "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe"
    )
    return str(win) if win.exists() else "adb"


def fb_code(timeout_s: int = 150) -> str | None:
    """Newest Meta 2FA code from the phone's notification shade (ADB)."""
    env = dict(os.environ, ADB=_adb_bin())
    try:
        out = subprocess.run(
            [sys.executable, str(FB_CODE), "--latest"],
            capture_output=True, text=True, timeout=timeout_s, env=env,
        )
        code = (out.stdout or "").strip().splitlines()
        if out.returncode == 0 and code:
            return code[0].strip()
    except Exception as exc:  # noqa: BLE001
        log(f"fb_code failed: {exc}")
    return None


def checkpoint_clicks(rounds: int = 4) -> list[str]:
    """Click through post-login checkpoint buttons; returns click log."""
    clicks = []
    for _ in range(rounds):
        r = sidecar("/debug/eval", {"script": _CHECKPOINT_JS}, timeout=60)
        result = r.get("result") or r.get("value") or ""
        clicks.append(str(result)[:60])
        if "clicked" not in str(result):
            break
        time.sleep(3)
    return clicks


def tg_alert(text: str) -> None:
    try:
        subprocess.run(
            [sys.executable, str(TG_ALERT), "p2", text],
            capture_output=True, timeout=30,
        )
    except Exception:  # noqa: BLE001
        pass


def validate() -> dict:
    return sidecar("/session/validate", timeout=120)


def attempt_login(env: dict[str, str], state: dict) -> str:
    """One full login attempt. Returns ok|2fa-failed|login-failed|throttled."""
    username = env.get("FACEBOOK_USERNAME", "")
    password = env.get("FACEBOOK_PASSWORD", "")
    if not username or not password:
        return "no-creds"

    state["last_login_attempt"] = time.time()
    save_state(state)

    r = sidecar("/login", {"username": username, "password": password}, timeout=180)
    log(f"login attempt -> {json.dumps(r)[:300]}")

    if r.get("two_factor_required"):
        code = fb_code()
        if not code:
            log("2FA required but no SMS code retrieved from phone")
            return "2fa-failed"
        log("2FA code retrieved — retrying login")
        r = sidecar(
            "/login",
            {"username": username, "password": password, "verification_code": code},
            timeout=180,
        )
        log(f"login retry w/ 2FA -> {json.dumps(r)[:300]}")

    clicks = checkpoint_clicks()
    log(f"checkpoint clicks: {clicks}")

    v = validate()
    if v.get("logged_in"):
        return "ok"
    return "login-failed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single probe+heal cycle")
    ap.add_argument("--min-interval", type=int, default=DEFAULT_MIN_INTERVAL,
                    help="seconds between login attempts (probe always runs)")
    args = ap.parse_args()

    env = load_env()
    state = load_state()

    while True:
        v = validate()
        if v.get("error"):
            log(f"sidecar unreachable: {v['error']}")
            if args.once:
                return 2
            time.sleep(300)
            continue

        if v.get("logged_in"):
            if state.get("alerted"):
                tg_alert("P2 ig-fb-session-heal: facebook sidecar session restored")
                state["alerted"] = False
                save_state(state)
            log("session ok")
            if args.once:
                return 0
            time.sleep(600)
            continue

        # logged out — throttle actual login attempts
        last = float(state.get("last_login_attempt") or 0)
        if time.time() - last < args.min_interval:
            log(f"session down; next login attempt in "
                f"{int(args.min_interval - (time.time() - last))}s")
            if args.once:
                return 3
            time.sleep(300)
            continue

        result = attempt_login(env, state)
        log(f"heal result: {result}")
        if result != "ok":
            state["failures"] = int(state.get("failures") or 0) + 1
            if not state.get("alerted") and state["failures"] >= 2:
                tg_alert(
                    "P2 ig-fb-session-heal: facebook sidecar session down, "
                    f"auto-login failing ({result}) — manual FB re-login may be needed"
                )
                state["alerted"] = True
            save_state(state)
            if args.once:
                return 4 if result == "ok" else 3
        else:
            state["failures"] = 0
            save_state(state)
            if args.once:
                return 0
        time.sleep(300)


if __name__ == "__main__":
    sys.exit(main())
