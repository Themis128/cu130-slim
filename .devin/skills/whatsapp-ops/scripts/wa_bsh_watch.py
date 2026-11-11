#!/usr/bin/env python3
"""wa_bsh_watch.py — watch Meta Business Support Home for account issues and
auto-appeal when Meta offers a review path.

Context: Meta's temporary Business-Settings write restriction
(error 2859015 / api_error_code 10) is an account-level cooldown. Per Meta
docs the durable remediation is: verify 2FA + business info (done), then
resolve any violation listed in Business Support Home — violations there can
be appealed via "Request review" (decisions ~24-48h).

This watcher drives the facebook-browser-sidecar debug API on :9226 only —
no Meta credentials are handled here. Each cycle:

  1. navigate to Business Support Home,
  2. snapshot the visible text,
  3. if a "Request review"/"Appeal" affordance exists on a violation,
     click it, fill the standard appeal note, submit, and record the
     violation hash so it never double-appeals,
  4. TG-alert (#ops) on NEW violations or a submitted appeal.

It does NOT try to lift cooldowns by brute force — it only acts on explicit
Meta-provided review paths. Safe states (no issues / no appeal button) are
logged and the loop continues.

Usage:  python3 wa_bsh_watch.py [--interval 1800] [--window 259200]
Log:    ~/logs/wa-bsh-watch.log  (override with WA_BSH_LOG)
State:  ~/.local/state/wa-bsh-watch/
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("FB_SIDECAR", "http://localhost:9226")
BUSINESS_ID = "1558125105019725"
BSH_URLS = [
    "https://business.facebook.com/latest/business-support-home"
    f"?business_id={BUSINESS_ID}",
    "https://www.facebook.com/business-support-home/",
]
LOG = os.environ.get("WA_BSH_LOG", os.path.expanduser("~/logs/wa-bsh-watch.log"))
STATE_DIR = os.path.expanduser("~/.local/state/wa-bsh-watch")
TG_ALERT = os.path.expanduser(
    "~/cu130-slim/.devin/skills/telegram-alert-ops/scripts/tg_alert.py"
)

APPEAL_NOTE = (
    "We believe this restriction was applied in error. Our business "
    "(cloudless.gr) complies with the WhatsApp Business and Commerce "
    "policies; all customer messaging is user-initiated and we do not send "
    "unsolicited bulk messages. Please review and restore full access."
)

REVIEW_LABELS = ("request review", "appeal", "request a review")
HEALTHY_MARKERS = ("no issues", "account is in good standing", "nothing to review")
ISSUE_MARKERS = ("restricted", "violation", "policy", "blocked", "disabled")


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def tg(severity: str, text: str) -> None:
    try:
        subprocess.run(
            [sys.executable, TG_ALERT, severity, text],
            timeout=30,
            capture_output=True,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"tg alert failed: {exc}")


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
    except Exception as exc:  # noqa: BLE001
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


# --- DOM helpers -------------------------------------------------------------

FIND_BTN_CONTAINS = """
(() => {
  const needle = %s.toLowerCase();
  const els = [...document.querySelectorAll(
    'div[role=button],button,[role=button],a')]
    .filter(e => e.offsetParent && e.innerText &&
           e.innerText.toLowerCase().includes(needle));
  if (!els.length) return null;
  const e = els[els.length - 1];
  const r = e.getBoundingClientRect();
  return {x: Math.round(r.x + r.width / 2),
          y: Math.round(r.y + r.height / 2),
          label: e.innerText.trim().slice(0, 80)};
})()
"""

FIND_TEXTAREA = """
(() => {
  const ta = [...document.querySelectorAll('textarea,[contenteditable=true]')]
    .filter(e => e.offsetParent).pop();
  if (!ta) return null;
  ta.focus();
  if (ta.tagName === 'TEXTAREA') {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ta, %s);
    ta.dispatchEvent(new Event('input', {bubbles: true}));
    ta.dispatchEvent(new Event('change', {bubbles: true}));
  } else {
    ta.innerText = %s;
    ta.dispatchEvent(new Event('input', {bubbles: true}));
  }
  return {filled: true};
})()
"""


def btn_contains(label: str):
    return eval_js(FIND_BTN_CONTAINS % json.dumps(label))


def fill_note() -> bool:
    res = eval_js(FIND_TEXTAREA % (json.dumps(APPEAL_NOTE), json.dumps(APPEAL_NOTE)))
    return bool(res and res.get("filled"))


def state_load() -> dict:
    path = os.path.join(STATE_DIR, "state.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def state_save(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, "state.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, path)


def violation_hash(text: str) -> str:
    # Hash only issue-relevant lines so cosmetic UI churn doesn't look new.
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if any(m in ln.lower() for m in ISSUE_MARKERS)
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def navigate() -> bool:
    for url in BSH_URLS:
        try:
            post("/debug/navigate", {"url": url}, timeout=60)
            sleep(7)
            txt = page_text()
            if txt:
                return True
        except Exception as exc:  # noqa: BLE001
            if any(s in str(exc) for s in SIDECAR_DOWN):
                return False
            log(f"navigate {url} error: {exc}")
    return False


def try_appeal(text: str, state: dict) -> str:
    """Look for a review affordance and submit the standard appeal note."""
    vh = violation_hash(text)
    for label in REVIEW_LABELS:
        btn = btn_contains(label)
        if not btn:
            continue
        log(f"review affordance found: '{btn.get('label')}' — clicking")
        click(btn["x"], btn["y"])
        sleep(4)

        # Some flows open a reason picker first; prefer anything containing
        # 'other'/'policy'/'mistake', else continue to the note field.
        for pick in ("other", "policy", "mistaken", "error"):
            p = btn_contains(pick)
            if p:
                click(p["x"], p["y"])
                sleep(2)
                break

        if not fill_note():
            log("appeal dialog opened but no text field found — leaving for human")
            tg("ops", f"[wa-bsh] appeal dialog needs manual text on Business Support Home (viol={vh})")
            return "appeal-manual"
        sleep(1)

        for submit_label in ("submit", "send", "confirm", "request review"):
            s = btn_contains(submit_label)
            if s:
                click(s["x"], s["y"])
                sleep(5)
                break
        state.setdefault("appealed", {})[vh] = time.strftime("%FT%T")
        state_save(state)
        log(f"appeal submitted for violation {vh}")
        tg(
            "ops",
            "[wa-bsh] submitted Business Support Home appeal "
            f"(viol={vh}) — Meta review ~24-48h",
        )
        return "appealed"
    return "no-appeal-path"


def cycle(state: dict) -> str:
    if not sidecar_alive():
        return "sidecar-down"
    try:
        health = get("/health", timeout=10)
        if not health.get("loggedIn", True):
            return "logged-out"
    except Exception:  # noqa: BLE001
        pass
    if not navigate():
        return "nav-failed"

    text = page_text()
    if not text:
        return "empty-page"
    low = text.lower()

    if any(m in low for m in HEALTHY_MARKERS) and not any(
        m in low for m in ISSUE_MARKERS
    ):
        return "healthy"

    vh = violation_hash(text)
    known = state.get("seen", {})
    appealed = state.get("appealed", {})

    if vh not in known:
        known[vh] = time.strftime("%FT%T")
        state["seen"] = known
        state_save(state)
        snippet = " ".join(text.split())[:300]
        log(f"NEW violation-ish state {vh}: {snippet}")
        tg("ops", f"[wa-bsh] Business Support Home shows issues ({vh}): {snippet[:200]}")

    if vh in appealed:
        return "already-appealed"

    return try_appeal(text, state)


def main() -> int:
    interval = (
        int(sys.argv[sys.argv.index("--interval") + 1])
        if "--interval" in sys.argv
        else 1800
    )
    window = (
        int(sys.argv[sys.argv.index("--window") + 1])
        if "--window" in sys.argv
        else 259200
    )
    deadline = time.time() + window
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    state = state_load()
    log(f"wa_bsh_watch started interval={interval}s window={window}s")

    while time.time() < deadline:
        result = cycle(state)
        log(f"cycle: {result}")
        stamp = os.path.join(STATE_DIR, "last-cycle.txt")
        with open(stamp, "w") as fh:
            fh.write(f"{time.strftime('%FT%T')} {result}\n")

        if result == "sidecar-down":
            log("sidecar unreachable — retrying in 5 min")
            sleep(300)
            continue
        if result == "logged-out":
            log("sidecar session logged out — needs relogin; exiting")
            return 4
        sleep(interval)

    log("window expired — exiting")
    return 3


if __name__ == "__main__":
    sys.exit(main())
