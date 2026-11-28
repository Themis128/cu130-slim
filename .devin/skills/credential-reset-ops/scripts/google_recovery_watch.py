#!/usr/bin/env python3
"""Google account-recovery SMS watcher — completes the themis.baltzakis
password reset unattended once the carrier delivers the code.

Context: Google account recovery for themis.baltzakis@gmail.com sends the
6-digit code to +30 697 777 7838 (the Galaxy S22). Carriers sometimes deliver
it as flash SMS (invisible to the inbox DB) or with long delay — this script
polls the Termux SMS lane via SSH and, when the code lands, drives the open
CDP challenge tab: enter code -> set new password -> store credential ->
run gmail_consent_drive.py to mint the Gmail OAuth token.

  python3 google_recovery_watch.py [account] [--minutes 25]

Exit 0 = fully done (password stored + consent token minted or consent
drive launched). Exit 3 = human gate remains. Exit 4 = code never arrived.
"""
import json
import re
import secrets
import string
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gmail-ops" / "scripts"))
import gmail_consent_drive as d  # noqa: E402
import gmail  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "themis.baltzakis@gmail.com"
MINUTES = 25
if "--minutes" in sys.argv:
    MINUTES = int(sys.argv[sys.argv.index("--minutes") + 1])

TERMUX_SSH = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
              "u0_a357@100.84.31.85", "termux-sms-list -l 60"]
PWD_KEY = "GOOGLE_ACCOUNT_PASSWORD_" + ACCOUNT.upper().replace(".", "_").replace("@", "_")


def poll_sms(since_epoch: float):
    """Return (code, raw) for the newest Google verification SMS, else None."""
    try:
        out = subprocess.run(TERMUX_SSH, capture_output=True, text=True, timeout=25)
        msgs = json.loads(out.stdout or "[]")
    except Exception:
        return None
    best = None
    for m in msgs:
        body = m.get("body", "")
        addr = str(m.get("address", ""))
        if not re.search(r"google|G-\d|verification code|κωδικός", body + addr, re.I):
            continue
        # received is 'YYYY-MM-DD HH:MM:SS' phone-local — accept today only;
        # Google only sent one code in this window.
        today = time.strftime("%Y-%m-%d")
        if today not in str(m.get("received", "")):
            continue
        code = None
        mm = re.search(r"G-(\d{6})", body) or re.search(r"\b(\d{6})\b", body)
        if mm:
            code = mm.group(1)
        if code and (best is None or str(m.get("received")) > str(best.get("received"))):
            best = {"code": code, "received": m.get("received"), "body": body[:100]}
    return best


def _gen_password() -> str:
    alpha = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alpha) for _ in range(14))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) \
                and any(c.isdigit() for c in pw):
            return pw + "!"


def page_state():
    cur = d.ev("location.href") or ""
    body = (d.ev("document.body ? document.body.innerText.slice(0,500) : ''") or "").lower()
    return cur, body


def main():
    start = time.time()
    deadline = start + MINUTES * 60
    print(f"[gwatch] watching Termux SMS for Google code -> {ACCOUNT} ({MINUTES}min)", flush=True)

    hit = None
    while time.time() < deadline:
        hit = poll_sms(start)
        if hit:
            break
        time.sleep(30)
    if not hit:
        print("[gwatch] no Google SMS within window — likely flash-SMS or carrier delay", flush=True)
        return 4
    print(f"[gwatch] code candidate {hit['code']} from {hit['received']}: {hit['body']}", flush=True)

    # --- enter the code on the challenge tab ---
    ws, mid, sid = d.ensure_tab()
    typed = d._type_into(
        'input[type="tel"], input[inputmode="numeric"], input[name*="code" i], '
        'input[id*="code" i], input[autocomplete="one-time-code"]', hit["code"])
    if not typed:
        typed = d._type_into('input[type="text"]:not([name="identifier"])', hit["code"])
    print(f"[gwatch] code typed={typed}", flush=True)
    time.sleep(1)
    d._trusted_click_text("next") or d._press_enter()

    # --- resolve: wrong code / password-create / signed-in ---
    res_deadline = time.time() + 120
    new_pw = None
    while time.time() < res_deadline:
        cur, body = page_state()
        print(f"[gwatch] {cur[:70]} | {body[:80].replace(chr(10),' ')}", flush=True)
        if "wrong" in body or "incorrect" in body or "expired" in body or "try again" in body:
            print("[gwatch] code rejected", flush=True)
            return 3
        if "create password" in body or "new password" in body or "reenter" in body \
                or ("password" in body and "challenge/pwd" not in cur):
            if new_pw is None:
                new_pw = _gen_password()
            ok = d._type_into('input[name="Passwd"], input[type="password"]:not([name="hiddenPassword"])', new_pw)
            if not ok:
                ok = d._type_into('input[type="password"]', new_pw)
            time.sleep(1)
            # confirm field if present
            d._type_into('input[name="confirmationPassword"], input[name="PasswdAgain"], input[type="password"]:nth-of-type(2)', new_pw)
            time.sleep(1)
            d._trusted_click_text("next") or d._press_enter()
            time.sleep(5)
            continue
        if "localhost:50000" in cur or "/signin/oauth/" in cur or "myaccount.google" in cur:
            break
        time.sleep(4)

    # --- store the new password ---
    if new_pw:
        gmail._save_env(PWD_KEY, new_pw)
        print(f"[gwatch] saved {PWD_KEY} to .env", flush=True)
        # mirror to generic key if unset
        if not gmail._env("GOOGLE_ACCOUNT_PASSWORD"):
            gmail._save_env("GOOGLE_ACCOUNT_PASSWORD", new_pw)

    # --- hand off to the consent drive (account now in chooser) ---
    print("[gwatch] launching gmail_consent_drive for token mint", flush=True)
    rc = subprocess.run(
        [sys.executable,
         str(Path(d.__file__).resolve().parent / "gmail_consent_drive.py"), ACCOUNT],
        timeout=600).returncode
    print(f"[gwatch] consent drive rc={rc}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
