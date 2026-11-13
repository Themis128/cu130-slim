#!/usr/bin/env python3
"""meta_lock_check.py — one-shot Meta account-lock / checkpoint diagnostic.

Reports everything needed to classify a Meta gate in SocialAuto:
  1. social_accounts rows for Meta platforms with status != active
  2. latest Meta security email in the mailbox (unlock/checkpoint notice)
  3. heal-bot log tails (ig-fb-session-heal, fb-session-heal, phone-fb-approve)
  4. facebook-browser-sidecar reachability
  5. whether a login attempt is currently pending (churn-guard countdown)

Read-only. Run from anywhere; uses docker exec for DB, IMAP for mail.
"""

import imaplib
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
ENV_CANDIDATES = [REPO / ".env", Path.home() / "cu130-slim" / ".env"]
LOG_DIR = Path.home() / "logs"
SIDECAR = "http://localhost:9226"  # facebook-browser-sidecar (CDP port)


def _env(key: str) -> str:
    for env_file in ENV_CANDIDATES:
        try:
            for line in env_file.read_text().splitlines():
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            continue
    return os.environ.get(key, "")


def psql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "social-postgres", "psql", "-U", "social_user",
         "-d", "social_automation", "-tAc", sql],
        capture_output=True, text=True, timeout=30,
    )
    return r.stdout.strip() if r.returncode == 0 else f"ERR: {r.stderr.strip()[:120]}"


def latest_meta_email() -> str:
    pw = _env("MAIL_TBALTZAKIS_PASSWORD") or _env("MAILBOX_PASSWORD")
    user = "tbaltzakis@cloudless.gr"
    if not pw:
        return "no MAIL_TBALTZAKIS_PASSWORD in .env"
    try:
        m = imaplib.IMAP4_SSL("192.168.1.130", 993, timeout=20)
        m.login(user, pw)
        m.select("INBOX")
        _, data = m.search(None, '(FROM "facebook" SINCE "01-Nov-2026")')
        ids = data[0].split()[-5:]
        out = []
        for i in ids:
            _, msg = m.fetch(i, "(BODY[HEADER.FIELDS (SUBJECT DATE)])")
            hdr = msg[0][1].decode("utf-8", "replace").replace("\r", " ")
            out.append(" | ".join(hdr.split("  ")[:2])[:160])
        m.logout()
        return "\n".join(out) if out else "no Meta emails since Nov 1"
    except Exception as e:  # noqa: BLE001
        return f"imap failed: {e}"


def heal_tail(log: str, n: int = 8) -> str:
    p = LOG_DIR / log
    if not p.exists():
        return f"{log}: missing"
    lines = [l for l in p.read_text(errors="replace").splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def sidecar_health() -> str:
    try:
        with urllib.request.urlopen(f"{SIDECAR}/health", timeout=5) as r:
            body = r.read()[:120]
            return f"{SIDECAR}/health -> {r.status} {body!r}"
    except Exception as e:  # noqa: BLE001
        return f"{SIDECAR} unreachable: {e}"


def main() -> int:
    print("== Meta account rows (non-active) ==")
    print(psql(
        "SELECT platform||' | '||username||' | '||status||' | updated '||updated_at::timestamp(0) "
        "FROM social_accounts WHERE platform IN ('facebook','instagram','threads','whatsapp','messenger') "
        "AND status <> 'active' ORDER BY platform"
    ) or "(all active)")

    print("\n== Latest Meta security emails ==")
    print(latest_meta_email())

    print("\n== ig-fb-session-heal tail ==")
    print(heal_tail("ig-fb-session-heal.log"))
    print("\n== fb-session-heal tail ==")
    print(heal_tail("fb-session-heal.log", 5))
    print("\n== phone-fb-approve tail ==")
    print(heal_tail("phone-fb-approve.log", 5))

    print("\n== facebook-browser-sidecar ==")
    print(sidecar_health())

    print("\n== Verdict hints ==")
    rows = psql(
        "SELECT count(*) FROM social_accounts WHERE platform IN ('facebook','instagram','threads','whatsapp') "
        "AND status='revoked'"
    )
    if rows and rows != "0":
        print(f"{rows} Meta account(s) revoked — mass revocation = account-level lock, not per-token expiry.")
        print("User action: approve 'Was this you?' / complete unlock on a trusted device (see SKILL.md).")
    else:
        print("No mass Meta revocation — check per-account token errors instead.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
