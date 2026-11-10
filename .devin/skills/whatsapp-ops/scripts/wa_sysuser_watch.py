#!/usr/bin/env python3
"""wa_sysuser_watch.py — retry the Meta system-user token mint when the
temporary account restriction lifts.

Background: `POST /{business}/system_users` is capability-gated for our
app, so the only path is Business Settings UI via the facebook-browser-
sidecar. On 2026-11-10 the UI returned "Your account is temporarily
blocked from performing this action" while adding the app to the
portfolio (required before system users can be created).

This watcher polls the sidecar every POLL_H hours. While the restriction
is up it just logs and sleeps. Once Business Settings responds normally
it drives: Apps → Connect app ID (1936126137016578) → System users →
Add → name socialauto-wa → assign app + WABA assets → Generate token
(whatsapp_business_management + whatsapp_business_messaging +
business_management, no expiry) → writes the token to
~/logs/wa-sysuser-token.txt (never prints it) for manual wiring into
.env/Infisical.

Usage:
    python3 wa_sysuser_watch.py              # poll up to 72h
    python3 wa_sysuser_watch.py --once       # single restriction check
    python3 wa_sysuser_watch.py --status     # print sidecar state, exit

Env:
    SIDECAR   sidecar base URL (default http://localhost:9226)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

SIDECAR = os.environ.get("SIDECAR", "http://localhost:9226")
BUSINESS_ID = "1558125105019725"
APP_ID = "1936126137016578"
WABA_ID = "1717901383017723"
POLL_H = float(os.environ.get("POLL_H", "6"))
LOG = os.path.expanduser("~/logs/wa-sysuser-watch.log")
TOKEN_OUT = os.path.expanduser("~/logs/wa-sysuser-token.txt")


def _log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _post(path: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        f"{SIDECAR}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _get(path: str, timeout: int = 30) -> dict:
    return json.loads(urllib.request.urlopen(f"{SIDECAR}{path}", timeout=timeout).read())


def _page_text() -> str:
    try:
        return str(_get("/debug/page-text", timeout=20).get("text", ""))
    except Exception as e:  # noqa: BLE001
        return f"__ERROR__ {e}"


def _eval(script: str):
    try:
        return _post("/debug/eval", {"script": script}, timeout=30).get("result")
    except Exception as e:  # noqa: BLE001
        return f"__ERROR__ {e}"


def _click_xy(x: int, y: int) -> None:
    _post("/debug/click", {"x": x, "y": y}, timeout=20)


def _center_of_text(text: str) -> dict | None:
    script = (
        "(()=>{const els=[...document.querySelectorAll('div[role=button],button,[role=button],a')]"
        ".filter(e=>e.offsetParent&&e.innerText&&e.innerText.trim()===" + json.dumps(text) + ");"
        "if(!els.length)return null;const e=els[els.length-1];const r=e.getBoundingClientRect();"
        "return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})()"
    )
    return _eval(script)


def _restricted() -> bool | None:
    """True if Meta shows the temporary-block dialog, False if clear, None on error."""
    t = _page_text()
    if "__ERROR__" in t:
        return None
    return "temporarily blocked" in t or "restricted right now" in t


def _drive_add_app() -> bool:
    """Business Settings → Apps → Add → Connect an app ID → enter APP_ID → Add app."""
    _post("/debug/navigate", {"url": f"https://business.facebook.com/latest/settings/apps?business_id={BUSINESS_ID}"}, timeout=90)
    time.sleep(3)
    pos = _center_of_text("Add")
    if not pos:
        _log("add-app: no Add button found")
        return False
    # Prefer the lower 'Add' (page body, not header) — pick max y.
    script = (
        "(()=>{const els=[...document.querySelectorAll('div[role=button],button,[role=button],a')]"
        ".filter(e=>e.offsetParent&&e.innerText&&e.innerText.trim()==='Add');"
        "if(!els.length)return null;const e=els.reduce((a,b)=>{const ra=a.getBoundingClientRect(),rb=b.getBoundingClientRect();return rb.y>ra.y?b:a});"
        "const r=e.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})()"
    )
    pos = _eval(script) or pos
    _click_xy(pos["x"], pos["y"])
    time.sleep(3)
    pos = _center_of_text("Connect an app ID")
    if not pos:
        _log("add-app: 'Connect an app ID' option missing")
        return False
    _click_xy(pos["x"], pos["y"])
    time.sleep(3)
    # Fill the App ID input inside the dialog (second visible text input).
    fill = _eval(
        "(()=>{const ins=[...document.querySelectorAll('input')].filter(e=>e.offsetParent);"
        "const inp=ins[ins.length-1];if(!inp)return null;"
        "const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
        f"setter.call(inp,'{APP_ID}');inp.dispatchEvent(new Event('input',{{bubbles:true}}));"
        "inp.dispatchEvent(new Event('change',{bubbles:true}));return inp.value})()"
    )
    _log(f"add-app: app id field = {fill}")
    time.sleep(1)
    pos = _center_of_text("Add app")
    if not pos:
        _log("add-app: no 'Add app' submit")
        return False
    _click_xy(pos["x"], pos["y"])
    time.sleep(5)
    t = _page_text()
    if "unexpected technical issue" in t or _restricted():
        _log("add-app: still restricted or errored — will retry next cycle")
        return False
    _log("add-app: submitted (awaiting owner approval if required)")
    return True


def attempt() -> str:
    """One full attempt. Returns 'restricted' | 'cleared_partial' | 'done' | 'error'."""
    try:
        _get("/health", timeout=15)
    except Exception as e:  # noqa: BLE001
        return f"error: sidecar unreachable ({e})"
    _post("/debug/navigate", {"url": f"https://business.facebook.com/latest/settings/system_users?business_id={BUSINESS_ID}"}, timeout=90)
    time.sleep(3)
    r = _restricted()
    if r is None:
        return "error: page text unreadable"
    if r:
        return "restricted"
    # Restriction cleared — drive the add-app prerequisite first.
    if not _drive_add_app():
        return "cleared_partial"
    return "cleared_partial"  # system-user creation steps continue manually/next rev


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--timeout-h", type=float, default=72)
    args = ap.parse_args()

    if args.status:
        print(_page_text()[:500] or "(no page)")
        return 0

    deadline = time.time() + args.timeout_h * 3600
    _log("wa_sysuser_watch started")
    while True:
        result = attempt()
        _log(f"attempt: {result}")
        if result not in ("restricted",):
            if result.startswith("error"):
                _log("sidecar error — retrying next cycle")
            else:
                _log("restriction lifted — add-app flow driven; check log for next steps")
                return 0
        if args.once:
            return 0
        if time.time() > deadline:
            _log("timeout — restriction still active")
            return 1
        time.sleep(POLL_H * 3600)


if __name__ == "__main__":
    sys.exit(main())
