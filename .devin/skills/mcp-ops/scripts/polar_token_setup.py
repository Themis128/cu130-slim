#!/usr/bin/env python3
"""Validate and write POLAR_ACCESS_TOKEN into the repo .env.

Polar personal access tokens are created in the Polar dashboard
(Settings → Developers → Access tokens, https://polar.sh/dashboard).
This script cannot mint one (Polar requires an interactive dashboard
session) — it validates a supplied token against the Polar API and
writes it to .env so the polar-mcp wrapper picks it up.

Usage:
  python polar_token_setup.py              # report current state
  python polar_token_setup.py <token>      # validate + write to .env
  python polar_token_setup.py --test       # test wrapper handshake if set
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
ENV_FILE = REPO / ".env"


def current_token() -> str:
    m = re.search(r"^POLAR_ACCESS_TOKEN=(.*)$", ENV_FILE.read_text(), re.M)
    return (m.group(1).strip().strip("\"'") if m else "")


def validate(token: str) -> dict:
    req = urllib.request.Request(
        "https://api.polar.sh/v1/users/me",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"ok": True, "status": r.status, "user": json.loads(r.read()).get("email", "?")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": str(e)[:80]}


def write_token(token: str) -> None:
    src = ENV_FILE.read_text()
    new, n = re.subn(r"^POLAR_ACCESS_TOKEN=.*$", f'POLAR_ACCESS_TOKEN="{token}"', src, flags=re.M)
    if n == 0:
        new = src.rstrip("\n") + f'\nPOLAR_ACCESS_TOKEN="{token}"\n'
    ENV_FILE.write_text(new)


def handshake() -> bool:
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "polar_setup", "version": "1"}}}) + "\n"
    try:
        p = subprocess.Popen([r"C:\Program Files\Git\bin\bash.exe",
                              str(REPO / ".devin/skills/polar-ops/scripts/polar-mcp.sh")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        out, _ = p.communicate(init, timeout=25)
        return '"result"' in out
    except Exception:
        return False


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tok = current_token()
    print(f"[polar] .env token: {'set (' + str(len(tok)) + ' chars)' if tok else 'EMPTY'}")

    if args:
        token = args[0]
        r = validate(token)
        if not r.get("ok"):
            print(f"[polar] token rejected by API ({r.get('status')}) — not writing")
            return 1
        write_token(token)
        print(f"[polar] validated ({r.get('user')}) and written to .env")
        tok = token

    if tok:
        r = validate(tok)
        print(f"[polar] API check: {'valid' if r.get('ok') else 'invalid (' + str(r.get('status')) + ')'}")
        if "--test" in sys.argv:
            print("[polar] wrapper handshake:", "OK" if handshake() else "FAIL")
    else:
        print("[polar] mint a token at https://polar.sh/dashboard → Settings → Developers,")
        print("        then run: python polar_token_setup.py <token>")
    return 0 if tok else 1


if __name__ == "__main__":
    sys.exit(main())
