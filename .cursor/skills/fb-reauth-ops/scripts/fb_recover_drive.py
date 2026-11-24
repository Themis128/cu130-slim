#!/usr/bin/env python3
"""Passwordless Facebook login via Meta's emailed recovery code.

Drives the :9229 debug Chrome through:
    /login/identify -> "Get code via email" -> /recover/code -> submit ->
    device-approval gate (push to the user's trusted phone) or logged-in.

The recovery code is read straight from the mailbox over
`ssh omv-ha 'sudo doveadm ...'` — no manual copy needed.

Gotchas burned in from live runs (see SKILL.md):
  * Meta re-issues the SAME code for every send inside one recovery
    session — a "stale" bounce usually means the submit failed, not the
    code. Only treat the code as dead when the page literally says so.
  * The ws connection DIES on every navigation — reconnect + re-query
    the target each step.
  * React inputs need the native value setter + `input` event; JS
    `.click()` no-ops on Meta buttons — use Input.dispatchMouseEvent
    after scrollIntoView({block:'center'}).
  * The 2FA "check your notifications" gate cannot be driven — a human
    must tap approve on the trusted phone (or pick "Try another way").

Usage (from WSL on the user's host):
  python3 fb_recover_drive.py status     # where is the FB tab now
  python3 fb_recover_drive.py drive      # run the whole code lane
  python3 fb_recover_drive.py code 78354608   # fill+submit a known code

Env:
  FB_CDP      CDP base URL (default http://192.168.96.1:9229)
  MAIL_SSH    ssh host for doveadm (default omv-ha)
  MAIL_USER   mailbox user (default tbaltzakis@cloudless.gr)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

CDP = os.environ.get("FB_CDP", "http://192.168.96.1:9229")
MAIL_SSH = os.environ.get("MAIL_SSH", "omv-ha")
MAIL_USER = os.environ.get("MAIL_USER", "tbaltzakis@cloudless.gr")

_id = [0]
_ws = None


def _tab() -> dict | None:
    for t in json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=10)):
        if t["type"] == "page" and "facebook" in t["url"]:
            return t
    return None


def _conn() -> None:
    global _ws
    import websocket  # websocket-client

    t = _tab()
    if not t:
        raise SystemExit("no facebook tab on CDP — open facebook.com in the debug chrome")
    _ws = websocket.create_connection(
        t["webSocketDebuggerUrl"], suppress_origin=True, timeout=30
    )


def _reconn() -> None:
    global _ws
    try:
        _ws.close()
    except Exception:
        pass
    time.sleep(1)
    _conn()


def _call(method: str, params: dict | None = None) -> dict:
    _id[0] += 1
    _ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
    while True:
        msg = json.loads(_ws.recv())
        if msg.get("id") == _id[0]:
            return msg.get("result", {})


def _ev(expr: str):
    return (
        _call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        .get("result", {})
        .get("value")
    )


def _click_label(label: str) -> dict | None:
    """Center of the button carrying exactly `label`; clicks it with real mouse events."""
    box = _ev(
        """(function(){const b=[...document.querySelectorAll(
        'div[role=button],button,input[type=submit]')].find(
        b=>/^%s$/.test((b.innerText||b.value||'').trim())); if(!b)return null;
        b.scrollIntoView({block:'center'});
        const r=b.getBoundingClientRect();
        return {x:r.x+r.width/2,y:r.y+r.height/2}})()"""
        % label
    )
    if not box:
        return None
    for t in ("mousePressed", "mouseReleased"):
        _call(
            "Input.dispatchMouseEvent",
            {
                "type": t,
                "x": box["x"],
                "y": box["y"],
                "button": "left",
                "clickCount": 1,
            },
        )
    return box


def _fill(selector_js: str, value: str) -> bool:
    return bool(
        _ev(
            """(function(){const inp=%s; if(!inp)return false;
            inp.scrollIntoView({block:'center'});
            const s=Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype,'value').set;
            s.call(inp,'%s');
            inp.dispatchEvent(new Event('input',{bubbles:true}));
            inp.dispatchEvent(new Event('change',{bubbles:true}));
            return inp.value==='%s'})()"""
            % (selector_js, value, value)
        )
    )


def _page() -> tuple[str, str]:
    return (
        _ev("location.href") or "",
        (_ev("document.body.innerText") or "")[:600],
    )


def _select_email_lane() -> None:
    _ev(
        """(function(){const t=[...document.querySelectorAll('*')].find(
        e=>e.innerText==='Get code via email');
        if(t)t.closest('label,div[role=button],div').click(); return 'ok'})()"""
    )


def newest_mail_code(since: str = "15min", poll_s: int = 90) -> str | None:
    """Newest 8-digit code in the mailbox, polling until it arrives."""
    deadline = time.time() + poll_s
    seen: set[str] = set()
    while time.time() < deadline:
        try:
            out = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "ConnectTimeout=8",
                    MAIL_SSH,
                    f"sudo doveadm search -u {MAIL_USER} mailbox INBOX "
                    f"SINCE {since} 2>/dev/null | tail -4",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            ).stdout
            uids = re.findall(r"(\d+)\s*$", out.strip(), re.M)
            for uid in reversed(uids):
                body = subprocess.run(
                    [
                        "ssh",
                        "-o",
                        "ConnectTimeout=8",
                        MAIL_SSH,
                        f"sudo doveadm fetch -u {MAIL_USER} body mailbox INBOX "
                        f"uid {uid} 2>/dev/null | grep -oE '[0-9]{{8}}' | head -2",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                ).stdout
                codes = re.findall(r"\d{8}", body)
                if codes and codes[0] not in seen:
                    return codes[0]
                seen.update(codes)
        except (subprocess.TimeoutExpired, OSError):
            pass
        time.sleep(6)
    return None


def submit_code(code: str) -> None:
    if not _fill(
        "document.querySelector('input[name=code],input[autocomplete=one-time-code],"
        "input[type=text],input[type=tel]')",
        code,
    ):
        raise SystemExit("no code input on page — not on the confirm step?")
    time.sleep(1)
    if not _click_label("Continue"):
        raise SystemExit("no Continue button")
    time.sleep(8)
    _reconn()


def cmd_drive() -> None:
    _conn()
    url, text = _page()
    if "recover" not in url:
        _call("Page.navigate", {"url": "https://www.facebook.com/login/identify"})
        time.sleep(6)
        _reconn()
        url, text = _page()
    if "Get code via email" in text:
        _select_email_lane()
        time.sleep(1)
        _click_label("Continue")
        time.sleep(6)
        _reconn()
    url, text = _page()
    if "Enter code" not in text:
        raise SystemExit(f"did not reach code page: {url} | {text[:120]}")
    print("code page reached — polling mailbox", file=sys.stderr)
    code = newest_mail_code()
    if not code:
        raise SystemExit("no code arrived in mailbox within timeout")
    print(f"entering code {code}", file=sys.stderr)
    submit_code(code)
    cmd_status()


def cmd_status() -> None:
    _conn()
    url, text = _page()
    state = "unknown"
    if "notifications on another device" in text:
        state = "waiting-device-approval (human: tap approve on the trusted phone)"
    elif "Enter code" in text:
        state = "on-code-page (code rejected or not submitted)"
    elif "Set a new password" in text or "new password" in text.lower():
        state = "password-reset page (recovery accepted)"
    elif url.rstrip("/") in ("https://www.facebook.com", "https://m.facebook.com"):
        state = "logged-in-or-picker (run fb_session_heal.py status)"
    print(f"url: {url[:150]}\nstate: {state}\ntext: {text[:300]}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        cmd_status()
    elif cmd == "drive":
        cmd_drive()
    elif cmd == "code" and len(sys.argv) > 2:
        _conn()
        submit_code(sys.argv[2])
        cmd_status()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
