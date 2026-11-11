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
  5. If the sidecar login stalls on Meta's "approve this login" prompt,
     drive the SAME account's live session on the engagement-browser
     bridge (:9223) to click the approval — the "other device" lane.

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


# Meta's logged-out homepage shows a profile picker ("<Name> Continue /
# Use another profile") instead of the login form — clicking the profile
# card re-logs the session with NO credentials and NO checkpoint.
# JS .click() doesn't reach the card's React handler reliably, so we
# extract the display name and use the sidecar's real-mouse /debug/click.
_PICKER_NAME_JS = """
(() => {
  const m = document.querySelector('[role=main]');
  if (!m || !/use another profile/i.test(m.innerText || '')) return '';
  const lines = (m.innerText || '').split('\\n')
      .map(s => s.trim()).filter(Boolean);
  return lines[0] || '';
})()
"""


# "Approve this login" prompt on OTHER logged-in Meta sessions — only
# fires when approval-context text is present; deny/"wasn't me" never
# matches. Runs on the engagement-browser bridge (:9223).
_BRIDGE_APPROVE_JS = """
(() => {
  const t = (document.body.innerText || '').slice(0, 4000);
  const approval = /was this you|approve (this|your) login|login attempt|it was me|this was me|unusual login|did you (try|attempt)/i.test(t);
  if (!approval) return 'no-approval-prompt';
  const els = [...document.querySelectorAll(
      'button,[role=button],a[role=link],div[tabindex]')];
  const vis = e => { const r = e.getBoundingClientRect();
                     return r.width > 0 && r.height > 0; };
  const txt = e => (e.innerText || e.getAttribute('aria-label') || '').trim();
  const deny = /wasn't me|not me|deny|decline|this was not|don't approve/i;
  const ok = /this was me|it was me|approve|yes,? (it|this) was me|confirm/i;
  const b = els.filter(vis).find(e => ok.test(txt(e)) && txt(e).length < 60 && !deny.test(txt(e)));
  if (b) { b.click(); return 'clicked:' + txt(b).slice(0, 40); }
  return 'approval-visible-no-button';
})()
"""


def bridge_eval(expr: str, timeout: int = 45) -> dict:
    """Evaluate JS on the engagement-browser bridge via docker exec curl.

    The bridge publishes no host port; engagement-browser is a node
    container with curl. Returns {} when the bridge is asleep/unreachable.
    """
    body = json.dumps({"expression": expr})
    try:
        out = subprocess.run(
            ["docker", "exec", "-i", "engagement-browser", "curl",
             "-sm30", "-X", "POST", "-H", "Content-Type: application/json",
             "--data-binary", "@-", "http://localhost:9223/session/evaluate"],
            input=body, capture_output=True, text=True, timeout=timeout,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return {}
        return json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        return {}


def bridge_approve() -> str:
    """Click Meta's 'approve this login' prompt on the bridge session."""
    bridge_eval("location.href='https://www.facebook.com/'", timeout=20)
    time.sleep(8)
    r = bridge_eval(_BRIDGE_APPROVE_JS)
    result = str(r.get("result") or "")[:80]
    log(f"bridge approve -> {result or r}")
    return result


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
    return termux_sms_code() or email_code()


def email_code(timeout_s: int = 45) -> str | None:
    """Fallback lane: Meta emails the login code — poll the mailbox.

    Meta/Facebook send "Your confirmation code" emails to the account
    address; the cloudless.gr mailbox is IMAP-readable (same creds as
    report-watch.py). Only codes from Meta senders, <45min old.
    """
    import imaplib
    import re

    env = load_env()
    password = env.get("MAIL_TBALTZAKIS_PASSWORD") or env.get("MAILBOX_PASSWORD")
    if not password:
        return None
    code_re = re.compile(
        r"(?:confirmation code|security code|login code|is your code)"
        r"[^0-9]{0,60}(\d{5,8})|(\d{6,8}) is your (?:Facebook|Instagram|Meta)",
        re.I,
    )
    try:
        mail = imaplib.IMAP4_SSL("192.168.1.130", 993, timeout=timeout_s)
        mail.login("tbaltzakis@cloudless.gr", password)
        mail.select("INBOX")
        # scan today's mail, filter Meta senders client-side
        since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - 86400))
        _, data = mail.search(None, "SINCE", since)
        ids = (data[0] or b"").split()[-40:]
        cutoff = time.time() - 45 * 60
        for mid in reversed(ids):
            _, msg_data = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)] BODY.PEEK[TEXT])")
            raw = b""
            for part in msg_data:
                if isinstance(part, tuple):
                    raw += part[1]
            if not raw:
                continue
            # date check on the raw header block
            head = raw.split(b"\r\n\r\n", 1)[0].decode("utf-8", "replace")
            if not re.search(r"facebook|meta\.com|instagram", head, re.I):
                continue
            m_date = re.search(r"Date:\s*(.+)", head)
            if m_date:
                try:
                    from email.utils import parsedate_to_datetime
                    if parsedate_to_datetime(m_date.group(1)).timestamp() < cutoff:
                        continue
                except Exception:  # noqa: BLE001
                    pass
            text = raw.decode("utf-8", "replace")
            m = code_re.search(text)
            if m:
                code = next(g for g in m.groups() if g)
                mail.logout()
                return code
        mail.logout()
    except Exception as exc:  # noqa: BLE001
        log(f"email_code failed: {exc}")
    return None


def termux_sms_code(timeout_s: int = 30) -> str | None:
    """Fallback lane: termux-sms-list over Tailscale ssh (:8022).

    Works when wireless ADB is off but Termux sshd is up — needs a key
    authorized on the phone (~/.ssh/id_rsa_win historically; tries any
    local key as fallback). Meta/WhatsApp sender lines only.
    """
    import re
    host = os.environ.get("PHONE_TAILNET_IP", "100.84.31.85")
    key_candidates = [
        str(HOME / ".ssh/id_rsa_win"),
        str(HOME / ".ssh/omv_id_rsa"),
        str(Path("/mnt/c/Users/tbaltzakis/.ssh/id_rsa")),
    ]
    for key in key_candidates:
        if not Path(key).exists():
            continue
        try:
            out = subprocess.run(
                ["ssh", "-p", "8022", "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no",
                 "-i", key, f"u0_a357@{host}", "termux-sms-list -l 8"],
                capture_output=True, text=True, timeout=timeout_s,
            )
            if out.returncode != 0:
                continue
            # Meta/WA senders first, then any 6-digit code in recent SMS
            lines = [l for l in out.stdout.splitlines()
                     if re.search(r"whatsapp|facebook|meta|auth", l, re.I)]
            m = re.search(r"\b(\d{3})[- ]?(\d{3})\b", " ".join(lines or out.stdout.splitlines()))
            if m:
                return m.group(1) + m.group(2)
        except Exception:  # noqa: BLE001
            continue
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


# Meta's notification-based 2FA ("Check your notifications on another
# device / Waiting for approval") — NOT a code. The sidecar page polls
# itself: if the user taps "This was me" on their phone/PC the session
# completes without any code. Detect that state so we can alert + wait.
_APPROVAL_WAIT_JS = """
(() => {
  const t = document.body.innerText || '';
  if (/waiting for approval|check your notifications on another device/i.test(t))
    return 'approval-waiting';
  return 'not-approval-wait';
})()
"""


# Poll the 2FA page WITHOUT navigating — /session/validate does
# page.goto(facebook.com) which would kill the pending approval flow.
_APPROVAL_POLL_JS = """
JSON.stringify({
  url: location.href,
  waiting: /waiting for approval|check your notifications on another device/i
      .test(document.body.innerText || ''),
})
"""


def approval_wait(state: dict, poll_s: int = 600) -> bool:
    """Alert once, then watch for the user to approve on their device.

    Never calls validate() inside the loop — it navigates away and
    aborts Meta's pending login. Polls location.href via /debug/eval;
    the flow is over once the URL leaves two_step_verification.
    """
    r = sidecar("/debug/eval", {"script": _APPROVAL_WAIT_JS}, timeout=60)
    if "approval-waiting" not in str(r.get("result") or ""):
        return False
    last_alert = float(state.get("last_approval_alert") or 0)
    if time.time() - last_alert > 7200:
        tg_alert(
            "P2 ig-fb-session-heal: FB login is WAITING for approval — "
            "tap 'This was me' on the Facebook notification (phone/PC) "
            "to finish the sidecar login automatically"
        )
        state["last_approval_alert"] = time.time()
        save_state(state)
    log("2FA is notification-approval — waiting for user tap (max 10m)")
    deadline = time.time() + poll_s
    while time.time() < deadline:
        time.sleep(20)
        r = sidecar("/debug/eval", {"script": _APPROVAL_POLL_JS}, timeout=60)
        try:
            poll = json.loads(str(r.get("result") or "{}"))
        except Exception:  # noqa: BLE001
            poll = {}
        url = str(poll.get("url") or "")
        if "two_step_verification" not in url and "two_factor" not in url:
            log(f"approval flow ended -> {url[:80]}")
            # GET /session persists storageState on success — validate() doesn't
            s = sidecar("/session", timeout=120)
            return bool(s.get("logged_in"))
        if not poll.get("waiting") and "two_step_verification" in url:
            # user switched to a code-entry variant — bail to caller
            return False
    return False


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

    # fast path: profile-picker "Continue as <name>" re-login (no creds,
    # no checkpoint risk — doesn't count against the attempt throttle)
    sidecar("/debug/navigate", {"url": "https://www.facebook.com/"}, timeout=90)
    time.sleep(5)
    nm = sidecar("/debug/eval", {"script": _PICKER_NAME_JS}, timeout=60)
    name = str(nm.get("result") or "").strip().strip('"')
    log(f"profile picker name -> {name!r}")
    if name:
        for target in (name, "Continue"):
            sidecar("/debug/click", {"text": target}, timeout=60)
            log(f"picker click -> {target!r}")
            time.sleep(10)
            v = validate()
            if v.get("logged_in"):
                return "ok"

    if not username or not password:
        return "no-creds"

    state["last_login_attempt"] = time.time()
    save_state(state)

    r = sidecar("/login", {"username": username, "password": password}, timeout=180)
    log(f"login attempt -> {json.dumps(r)[:300]}")

    was_2fa = False
    if r.get("two_factor_required"):
        was_2fa = True
        code = fb_code()
        if code:
            log("2FA code retrieved — retrying login")
            r = sidecar(
                "/login",
                {"username": username, "password": password,
                 "verification_code": code},
                timeout=180,
            )
            log(f"login retry w/ 2FA -> {json.dumps(r)[:300]}")
        else:
            log("2FA required but no SMS code retrieved from phone")
            if approval_wait(state):
                return "ok"

    clicks = checkpoint_clicks()
    log(f"checkpoint clicks: {clicks}")

    v = validate()
    if v.get("logged_in"):
        return "ok"

    # approval-from-another-device lane: the bridge holds a live Meta
    # session — let it click the "was this you" prompt, then re-check.
    appr = bridge_approve()
    if appr.startswith("clicked"):
        time.sleep(10)
        v = validate()
        if v.get("logged_in"):
            return "ok"
    return "2fa-failed" if was_2fa else "login-failed"


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
