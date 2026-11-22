#!/usr/bin/env python3
"""cred_verify.py — live-probe platform login credentials from the store.

Answers "does the canonical credential actually work?" — the check that
was missing on 2026-11-22 (store/env converged but Meta rejected the
password on both IG and FB).

Probes:
  facebook   POST facebook-browser-sidecar:9226/login  (real login attempt)
  instagram  ig_login.py headless                  (--ig-login only;
             headless IG logins carry checkpoint risk — use sparingly)

Safety:
  - Facebook probe honours a min-interval state file (default 6h) so a
    cron/watch loop can't hammer Meta login. --force overrides.
  - Never prints credential values; reports status words only.

Usage:
  cred_verify.py                 # facebook probe (interval-gated)
  cred_verify.py --force         # ignore interval gate
  cred_verify.py --ig-login      # also run the IG headless probe
  cred_verify.py --status        # show last probe results, no probing

Exit 0 = all probed platforms accepted, 1 = rejection/failure, 2 = infra.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
MAIN = REPO
try:
    out = subprocess.run(
        ["git", "-C", str(REPO), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, timeout=10).stdout
    for line in out.splitlines():
        if line.startswith("worktree "):
            MAIN = Path(line.split(" ", 1)[1])
            break
except Exception:
    pass

STATE = Path.home() / ".local/state/cred-verify.json"
SIDECAR = os.environ.get("FACEBOOK_SIDECAR_URL", "http://127.0.0.1:9226")
MIN_INTERVAL_S = int(os.environ.get("CRED_VERIFY_MIN_INTERVAL", 6 * 3600))
IG_LOGIN = Path.home() / "cu130-slim/social-automation/backend/scripts/ig_login.py"
IG_ENV = Path("/tmp/igtest_env_verify.env")


def _load_env() -> dict[str, str]:
    env = {}
    for line in (MAIN / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def _fb_probe(env: dict[str, str]) -> str:
    body = json.dumps({
        "username": env.get("FACEBOOK_USERNAME", ""),
        "password": env.get("FACEBOOK_PASSWORD", ""),
    }).encode()
    req = urllib.request.Request(
        f"{SIDECAR}/login", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            d = json.loads(r.read())
    except Exception as exc:
        return f"ERROR({type(exc).__name__})"
    if str(d.get("logged_in")).lower() == "true" or d.get("status") == "ok" and d.get("logged_in") is True:
        return "OK"
    msg = str(d.get("message") or d.get("error") or "").lower()
    # 2FA-required means the password itself was ACCEPTED — the credential
    # is valid; only the second factor is pending. Not a rejection.
    if "2fa required" in msg or "two_factor" in msg or "verification_code" in msg:
        return "OK(2fa-required)"
    if "invalid" in msg or "incorrect" in msg or "failed" in msg:
        return "REJECTED"
    return f"UNCLEAR({str(d.get('message'))[:60]})"


def _ig_probe(env: dict[str, str]) -> str:
    if not IG_LOGIN.exists():
        return "SKIP(no ig_login.py)"
    # ig_login reads IG_ENV_FILE — build a probe env from current .env
    IG_ENV.write_text((MAIN / ".env").read_text())
    env_vars = dict(os.environ, IG_ENV_FILE=str(IG_ENV),
                    LD_LIBRARY_PATH="/tmp/alsa-lib/usr/lib/x86_64-linux-gnu")
    py = os.environ.get("IG_PYTHON", "/home/tbaltzakis/.venv-igreset/bin/python")
    try:
        p = subprocess.run([py, str(IG_LOGIN), "--headless"],
                           capture_output=True, text=True, timeout=300,
                           env=env_vars, cwd="/tmp")
    except Exception as exc:
        return f"ERROR({type(exc).__name__})"
    out = (p.stdout or "") + (p.stderr or "")
    if "bad-credentials" in out:
        return "REJECTED"
    if p.returncode == 0:
        return "OK"
    if "checkpoint" in out or "2fa" in out.lower():
        return "WALL(checkpoint/2fa)"
    return f"UNCLEAR(rc={p.returncode})"


def main() -> int:
    args = set(sys.argv[1:])
    if "--status" in args:
        print(json.dumps(_state(), indent=2))
        return 0

    st = _state()
    now = time.time()
    force = "--force" in args
    if not force and now - float(st.get("ts", 0)) < MIN_INTERVAL_S:
        print(f"interval gate: last probe {int((now - st.get('ts', 0)) / 3600)}h ago "
              f"(min {MIN_INTERVAL_S // 3600}h) — use --force")
        return 0

    env = _load_env()
    results = {"ts": now, "platforms": {}}

    fb = _fb_probe(env)
    results["platforms"]["facebook"] = fb
    print(f"facebook   {fb}")

    if "--ig-login" in args:
        ig = _ig_probe(env)
        results["platforms"]["instagram"] = ig
        print(f"instagram  {ig}")

    _save_state(results)
    bad = any(v.startswith(("REJECTED", "ERROR", "UNCLEAR"))
              for v in results["platforms"].values())
    print("\n" + ("credential rejection detected — store a verified "
                  "value via cred_set.py" if bad else "all probes passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
