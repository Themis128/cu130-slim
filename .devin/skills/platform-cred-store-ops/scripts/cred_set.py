#!/usr/bin/env python3
"""cred_set.py — canonical write path for platform login credentials.

Writes a platform credential ONLY through the SocialAuto secret store
(POST /api/v1/secrets/{key} → D1 → Postgres → container /app/.env), then
realigns the host .env mirrors and verifies store↔mirror hash parity.

Usage:
  cred_set.py KEY                 # prompts for value (getpass, never argv)
  cred_set.py KEY --value-stdin   # reads value from stdin
  cred_set.py KEY --desc "why"    # optional store description

Never pass the secret as an argv value — shell history leaks it.
Exit 0 = written + mirrors converged, 1 = write/verify failed, 2 = infra.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / ".devin/skills/_lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skill_http import request_status, set_secret, social_api  # noqa: E402

import cred_audit  # noqa: E402  (resolves main checkout + env parsing)


def _h(v: str | None) -> str:
    return "-" if not v else hashlib.sha256(v.encode()).hexdigest()[:12]


def _read_value(key: str) -> str:
    if "--value-stdin" in sys.argv:
        return sys.stdin.read().rstrip("\n")
    return getpass.getpass(f"new value for {key}: ")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    key = args[0]
    desc = ""
    if "--desc" in sys.argv:
        i = sys.argv.index("--desc")
        desc = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""

    value = _read_value(key)
    if not value:
        print("empty value — aborting", file=sys.stderr)
        return 2

    # 1. Canonical write via SocialAuto
    set_secret(key, value, desc)

    # 2. Align host .env mirrors to the store (store is canonical)
    subprocess.run(
        [sys.executable,
         str(Path(__file__).parent / "cred_audit.py"), "--fix-mirror"],
        check=False,
    )

    # 3. Verify: store hash == both mirror hashes
    api, tok = social_api()
    code, body = request_status("GET", f"{api}/api/v1/secrets/{key}", token=tok)
    if code != 200:
        print(f"verify failed: GET secrets/{key} -> {code}", file=sys.stderr)
        return 1
    store_val = json.loads(body).get("value")
    want = _h(store_val)
    envs = {n: cred_audit._parse_env(p) for n, p in cred_audit.ENV_FILES.items()}
    ok = all(_h(e.get(key)) == want for e in envs.values())
    print(f"verify {key}: store={want} " +
          " ".join(f"{n}={_h(e.get(key))}" for n, e in envs.items()) +
          ("  OK" if ok else "  DIVERGED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
