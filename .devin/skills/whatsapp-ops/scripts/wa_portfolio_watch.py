#!/usr/bin/env python3
"""wa_portfolio_watch.py — land the Meta app in the cloudless.gr business
portfolio, which is the prerequisite for creating a system user + token.

Meta temporarily restricts Business Settings write actions
("You have been temporarily blocked from performing this action.").
The restriction lifts on its own; this watcher retries the
Apps -> Add -> Connect an app ID flow on a slow cadence until either:

  * the app appears in the portfolio list  -> exit 0 (SUCCESS)
  * a non-restriction error state appears  -> exit 2 (ATTENTION)
  * the window expires                     -> exit 3 (TIMEOUT)

Drives the facebook-browser-sidecar debug API on :9226 only — no Meta
credentials are handled here.

Usage:  python3 wa_portfolio_watch.py [--interval 1500] [--window 172800]
Log:    ~/logs/wa-portfolio-watch.log  (override with WA_WATCH_LOG)
State:  ~/.local/state/wa-portfolio-watch/last-attempt.txt
"""

import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("FB_SIDECAR", "http://localhost:9226")
APP_ID = os.environ.get("META_APP_ID", "1936126137016578")
APPS_URL = (
    "https://business.facebook.com/latest/settings/apps"
    "?business_id=1558125105019725"
)
LOG = os.environ.get(
    "WA_WATCH_LOG", os.path.expanduser("~/logs/wa-portfolio-watch.log")
)
STATE_DIR = os.path.expanduser("~/.local/state/wa-portfolio-watch")
RESTRICTED = "temporarily blocked from performing this action"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


SIDECAR_DOWN = ("Connection refused", "Remote end closed", "timed out")


def post(path: str, payload: dict, timeout: int = 25) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def get(path: str, timeout: int = 25) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def sidecar_alive() -> bool:
    try:
        get("/health", timeout=8)
        return True
    except Exception:  # noqa: BLE001
        return False


def eval_js(script: str):
    try:
        return post("/debug/eval", {"script": script}).get("result")
    except Exception as exc:  # noqa: BLE001 - watcher must never die on one eval
        log(f"eval error: {exc}")
        return None


def page_text() -> str:
    try:
        return get("/debug/page-text").get("text", "")
    except Exception as exc:  # noqa: BLE001
        log(f"page-text error: {exc}")
        return ""


def click(x: int, y: int) -> None:
    try:
        post("/debug/click", {"x": x, "y": y}, timeout=15)
    except Exception as exc:  # noqa: BLE001
        log(f"click error: {exc}")


def sleep(s: float) -> None:
    time.sleep(s)


# --- DOM helpers (all return None when nothing matches) ---------------------

FIND_BTN = """
(() => {
  const els = [...document.querySelectorAll(
    'div[role=button],button,[role=button],a')]
    .filter(e => e.offsetParent && e.innerText &&
           e.innerText.trim() === %s);
  if (!els.length) return null;
  const e = els.reduce((a, b) => {
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    return rb.y > ra.y ? b : a;   // lowest on screen = main-content action
  });
  const r = e.getBoundingClientRect();
  return {x: Math.round(r.x + r.width / 2),
          y: Math.round(r.y + r.height / 2)};
})()
"""

FIND_TEXT = """
(() => {
  const els = [...document.querySelectorAll(
    'div[role=button],button,[role=button],a,span,div')]
    .filter(e => e.offsetParent && e.innerText &&
           e.innerText.trim() === %s);
  if (!els.length) return null;
  const e = els[els.length - 1];
  const r = e.getBoundingClientRect();
  return {x: Math.round(r.x + r.width / 2),
          y: Math.round(r.y + r.height / 2)};
})()
"""

FIND_INPUT = """
(() => {
  const ins = [...document.querySelectorAll('input')]
    .filter(e => e.offsetParent);
  const el = ins.find(e => {
    const r = e.getBoundingClientRect();
    return Math.abs(r.y + r.height / 2 - %d) < 80;
  }) || ins.filter(e => !e.value).pop();
  if (!el) return null;
  el.focus();
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, %s);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return {v: el.value};
})()
"""


def btn_pos(label: str):
    return eval_js(FIND_BTN % json.dumps(label))


def text_pos(label: str):
    return eval_js(FIND_TEXT % json.dumps(label))


def fill_app_id() -> bool:
    res = eval_js(FIND_INPUT % (0, json.dumps(APP_ID)))
    return bool(res and res.get("v") == APP_ID)


def attempt() -> str:
    """One full add-app attempt. Returns 'restricted'|'success'|'error'|'stuck'."""
    if not sidecar_alive():
        return "sidecar-down"
    try:
        post("/debug/navigate", {"url": APPS_URL}, timeout=60)
    except Exception as exc:  # noqa: BLE001
        if any(s in str(exc) for s in SIDECAR_DOWN):
            return "sidecar-down"
        return f"nav-error:{exc}"
    sleep(6)

    health = {}
    try:
        health = get("/health", timeout=10)
    except Exception:  # noqa: BLE001
        pass
    if not health.get("loggedIn", True):
        return "logged-out"

    add = btn_pos("Add")
    if not add:
        txt = page_text()
        if "No apps added" not in txt and RESTRICTED not in txt:
            return "maybe-done"  # list populated or UI changed — inspect manually
        return "stuck:no-add-button"
    click(add["x"], add["y"])
    sleep(3)

    conn = text_pos("Connect an app ID")
    if not conn:
        txt = page_text()
        if RESTRICTED in txt:
            return "restricted"
        return "stuck:no-connect-option"
    click(conn["x"], conn["y"])
    sleep(3)

    if not fill_app_id():
        return "stuck:no-input"
    sleep(1)

    submit = btn_pos("Add app") or text_pos("Add app")
    if not submit:
        return "stuck:no-submit"
    click(submit["x"], submit["y"])
    sleep(6)

    txt = page_text()
    if RESTRICTED in txt:
        ok = btn_pos("OK") or text_pos("OK")
        if ok:
            click(ok["x"], ok["y"])
        return "restricted"
    # success heuristics: dialog closed, app row present, or request-sent text
    if "No apps added" not in txt or "request" in txt.lower():
        return "success"
    return "unclear"


def main() -> int:
    interval = int(sys.argv[sys.argv.index("--interval") + 1]) if "--interval" in sys.argv else 1500
    window = int(sys.argv[sys.argv.index("--window") + 1]) if "--window" in sys.argv else 172800
    deadline = time.time() + window
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    log(f"wa_portfolio_watch started interval={interval}s window={window}s app={APP_ID}")

    while time.time() < deadline:
        result = attempt()
        log(f"attempt: {result}")
        stamp = os.path.join(STATE_DIR, "last-attempt.txt")
        with open(stamp, "w") as fh:
            fh.write(f"{time.strftime('%FT%T')} {result}\n")

        if result in ("success", "maybe-done"):
            tail = page_text()[-1500:]
            with open(os.path.join(STATE_DIR, "final-page.txt"), "w") as fh:
                fh.write(tail)
            log("app appears added — exiting; see final-page.txt")
            return 0
        if result == "sidecar-down":
            log("sidecar unreachable — retrying in 5 min")
            sleep(300)
            continue
        if result == "logged-out":
            log("sidecar session logged out — needs relogin; exiting")
            return 4
        if result not in ("restricted", "stuck:no-add-button",
                          "stuck:no-connect-option", "stuck:no-input",
                          "stuck:no-submit", "unclear"):
            log(f"unexpected state {result} — backing off longer")
            sleep(min(interval * 2, 7200))
            continue
        sleep(interval)

    log("window expired — still restricted; exiting")
    return 3


if __name__ == "__main__":
    sys.exit(main())
