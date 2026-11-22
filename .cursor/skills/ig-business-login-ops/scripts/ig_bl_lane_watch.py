#!/usr/bin/env python3
"""ig_bl_lane_watch.py — unattended revival of the instagram2 (business_login) lane.

Closes the last manual gap in IG publish automation:

  1. Probe the business_login social_accounts row live
     (graph.instagram.com/me with the stored token) — cheap, every run.
  2. Token dead -> cooldown check (default 6h between drive attempts —
     repeated OAuth consents themselves look suspicious to Meta) ->
     run ig_bl_session_drive.py, which injects the stored private-API
     session cookies into the CDP debug Chrome (:9229 on the Windows
     host), mints the instagram2 authorize URL via social-api, and lets
     the consent->callback exchange re-mint the 60-day token.
  3. Re-probe and alert via tg_alert.py on state transitions
     (dead->healed, still-dead, no-session-blob).

The routine keep-alive is already automated elsewhere:
  - celery beat ``refresh-instagram-tokens`` (daily 03:20) refreshes
    long-lived tokens >=24h old via graph.instagram.com/refresh_access_token
  - ``instagram_lane_guard`` (15min) repoints failed fb_onboarding
    targets onto this lane
This watcher only fires when the token is truly dead (revoked/expired
past refresh) — the case that previously needed a human.

Prereqs: CDP debug Chrome reachable at http://<host-ip>:9229 with a live
instagram.com session (cookie transplant source: the account row's
private_api_session_* blob, maintained by instagram-ops).

Log:   ~/logs/ig-bl-lane-watch.log
State: ~/.local/state/ig-bl-watch/state.json
Usage: ig_bl_lane_watch.py [--once] [--min-interval 21600] [--cdp URL]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
REPO = HOME / "cu130-slim"
DRIVE = REPO / ".devin/skills/ig-business-login-ops/scripts/ig_bl_session_drive.py"
TG_ALERT = REPO / ".devin/skills/telegram-alert-ops/scripts/tg_alert.py"
STATE_DIR = HOME / ".local/state/ig-bl-watch"
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = HOME / "logs/ig-bl-lane-watch.log"
DEFAULT_MIN_INTERVAL = 6 * 3600  # 6h between re-mint attempts


def _log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _host_ip() -> str:
    try:
        out = subprocess.run(
            ["sh", "-c", "ip route | awk '/default/ {print $3; exit}'"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return out or "192.168.96.1"
    except Exception:
        return "192.168.96.1"


def _cdp_alive(cdp: str) -> bool:
    try:
        urllib.request.urlopen(cdp + "/json/version", timeout=5).read()
        return True
    except Exception:
        return False


def _open_tab(cdp: str, url: str) -> dict:
    req = urllib.request.Request(
        cdp + "/json/new?" + urllib.parse.quote(url, safe=":/?&=%,.{}"), method="PUT"
    )
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def _tab_eval(tab: dict, expr: str):
    import websocket  # type: ignore

    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=20)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    ws.settimeout(15)
    try:
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == 1:
                return r.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()


def _ensure_ig_session(cdp: str) -> bool:
    """Make sure the CDP Chrome holds a live instagram.com session before
    the OAuth drive. When Meta killed the session (password change etc.)
    instagram.com shows a profile chooser — clicking the saved
    'cloudless.gr' profile restores it with NO password/2FA (verified
    2026-11-22). Returns True when a session appears usable."""
    tab = _open_tab(cdp, "https://www.instagram.com/")
    time.sleep(6)
    for attempt in range(3):
        state = _tab_eval(
            tab,
            """JSON.stringify({
                url: location.href,
                loginForm: !!document.querySelector('input[name=password],input[name=username]'),
                chooser: [...document.querySelectorAll('span')].some(e=>e.textContent.trim()==='cloudless.gr'),
            })""",
        )
        try:
            st = json.loads(state or "{}")
        except Exception:
            st = {}
        if st.get("loginForm") and not st.get("chooser"):
            _log("IG login wall without saved profile — cannot self-heal")
            return False
        if st.get("chooser"):
            clicked = _tab_eval(
                tab,
                """(()=>{const s=[...document.querySelectorAll('span')].find(e=>e.textContent.trim()==='cloudless.gr');
                if(!s) return 'gone';
                let el=s; while(el && el.getAttribute('role')!=='button') el=el.parentElement;
                if(!el) return 'nobtn'; el.click(); return 'clicked';})()""",
            )
            _log(f"IG profile chooser — clicked cloudless.gr ({clicked})")
            time.sleep(8)
            continue
        # No login form, no chooser → session looks live
        _log(f"IG session live ({(st.get('url') or '')[:60]})")
        return True
    _log("IG session rescue inconclusive after 3 attempts")
    return False


def _probe() -> bool:
    """True when the business_login token answers graph.instagram.com/me."""
    if not DRIVE.is_file():
        _log(f"drive script missing: {DRIVE}")
        return False
    r = subprocess.run(
        [sys.executable, str(DRIVE), "--skip-drive"],
        capture_output=True, text=True, timeout=180,
    )
    ok = r.returncode == 0
    tail = (r.stdout + r.stderr).strip().splitlines()
    _log(f"probe rc={r.returncode} {'HEALTHY' if ok else 'DEAD'}" + (f" | {tail[-1][:160]}" if tail else ""))
    return ok


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def _alert(text: str) -> None:
    if not TG_ALERT.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(TG_ALERT), "ops", text[:1500]],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        _log(f"alert failed: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--min-interval", type=int, default=DEFAULT_MIN_INTERVAL)
    ap.add_argument("--cdp", default=None, help="CDP base, default http://<host-ip>:9229")
    args = ap.parse_args()

    if _probe():
        st = _state()
        if st.get("dead"):
            _alert("IG business_login lane healed — token probe green again.")
            _log("lane recovered")
        _save_state({"dead": False, "checked_at": time.time()})
        return 0

    st = _state()
    now = time.time()
    last_drive = float(st.get("last_drive_at") or 0)
    if now - last_drive < args.min_interval:
        _log(f"lane dead but drive cooldown active ({int((args.min_interval - (now - last_drive)) / 60)}min left)")
        return 0

    cdp = args.cdp or f"http://{_host_ip()}:9229"
    if not _cdp_alive(cdp):
        _alert(f"IG business_login token dead AND CDP chrome unreachable at {cdp} — manual re-auth needed.")
        _log(f"CDP unreachable: {cdp}")
        _save_state({**st, "dead": True, "checked_at": now})
        return 1

    # Revive the browser IG session first — the OAuth consent needs a live
    # instagram.com session; Meta's profile chooser restores it without
    # credentials when a saved profile exists.
    if not _ensure_ig_session(cdp):
        _alert(
            "IG business_login token dead and no usable IG browser session "
            "(login wall, no saved profile) — manual login required."
        )
        _save_state({**st, "dead": True, "checked_at": now})
        return 1

    _log(f"lane dead — driving re-mint via {DRIVE.name} on {cdp}")
    r = subprocess.run(
        [sys.executable, str(DRIVE), "--cdp", cdp],
        capture_output=True, text=True, timeout=600,
    )
    out = (r.stdout + r.stderr).strip()
    _log(f"drive rc={r.returncode} | {out[-400:]}")
    _save_state({**st, "dead": True, "last_drive_at": now, "checked_at": now, "last_rc": r.returncode})

    if r.returncode == 0 and _probe():
        _alert("IG business_login lane auto-healed — token re-minted via session drive.")
        _save_state({"dead": False, "checked_at": time.time()})
        return 0

    _alert(
        "IG business_login token dead — automated re-mint attempted and failed. "
        "Check ~/logs/ig-bl-lane-watch.log; may need manual OAuth (ig_bl_session_drive.py)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
